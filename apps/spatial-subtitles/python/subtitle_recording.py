# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in, bounded local training clips and transcript logs; never saves images."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
import uuid


def recording_root():
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "spatial-subtitles/recordings"
    )


def private_directory(path):
    if path.is_symlink():
        raise OSError("Recording directory is a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise OSError("Recording directory has another owner")
    path.chmod(0o700)


def write_private(path, data, append=False):
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_NOFOLLOW
        | (os.O_APPEND if append else os.O_EXCL)
    )
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "ab" if append else "wb") as out:
        os.fchmod(out.fileno(), 0o600)
        out.write(data)


class Recorder:
    def __init__(self, root=None):
        self.root = Path(root) if root else recording_root()
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.generation = 0
        self.settings = None
        self.pending = 0
        self.closed = False
        self.state = "Recording off"
        self.last_ocr = None

    def configure(self, settings):
        # Serializes with disk writes. Once disable returns, old jobs cannot write.
        with self.lock:
            self.generation += 1
            self.settings = settings
            self.last_ocr = None
            active = (
                settings.save_training_audio or settings.log_speech or settings.log_ocr
            )
            self.state = "Local recording enabled" if active else "Recording off"

    def record(self, kind, lines, captured, pcm=None):
        with self.lock:
            s = self.settings
            if self.closed or s is None or not lines:
                return
            active = (
                kind == "speech"
                and s.enabled
                and (s.save_training_audio or s.log_speech)
            ) or (kind == "ocr" and s.ocr_enabled and s.log_ocr)
            if not active:
                return
            if kind == "ocr":
                fingerprint = tuple(
                    (x.get("source_text"), x.get("text")) for x in lines
                )
                if (
                    self.last_ocr
                    and fingerprint == self.last_ocr[0]
                    and time.monotonic() - self.last_ocr[1] < 30
                ):
                    return
                self.last_ocr = (fingerprint, time.monotonic())
            if self.pending >= 16:
                self.state = "Recording queue full; newest item skipped"
                return
            utc = time.time() - max(0, time.monotonic() - captured)
            record = dict(
                version=1,
                id=uuid.uuid4().hex,
                kind=kind,
                captured_at=datetime.fromtimestamp(utc, timezone.utc).isoformat(),
                recognition_backend="openvino",
                recognition_model=s.local_speech_model
                if kind == "speech"
                else s.local_ocr_model,
                translation_model=s.local_translation_model,
                output_language=s.language if kind == "speech" else s.ocr_language,
                lines=[
                    {
                        k: x[k]
                        for k in (
                            "source_text",
                            "text",
                            "speaker",
                            "offset",
                            "box",
                            "confidence",
                        )
                        if k in x
                    }
                    for x in lines
                ],
            )
            self.pending += 1
            return self.executor.submit(self._save, self.generation, s, record, pcm)

    def _prune(self, settings, incoming):
        entries = []
        cutoff = time.time() - settings.recording_retention_days * 86400
        # Only this module's flat directories/files; never follow links or delete unrelated data.
        for folder, suffixes in [
            ("captures", (".json", ".wav")),
            ("transcripts", (".jsonl",)),
        ]:
            directory = self.root / folder
            if directory.is_symlink():
                raise OSError("Unsafe recording directory")
            if not directory.exists():
                continue
            for path in directory.iterdir():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or path.suffix not in suffixes:
                    continue
                if info.st_mtime < cutoff:
                    path.unlink()
                else:
                    entries.append((info.st_mtime, path, info.st_size))
        entries.sort(key=lambda x: x[0])
        total = sum(x[2] for x in entries)
        limit = settings.recording_max_mb * 1024 * 1024
        removed = set()
        for _, path, size in entries:
            if total + incoming <= limit and len(entries) - len(removed) < 10000:
                break
            if path in removed:
                continue
            group = [
                x
                for x in entries
                if x[1] == path
                or (
                    path.parent.name == "captures"
                    and x[1].parent == path.parent
                    and x[1].stem == path.stem
                )
            ]
            for _, item, count in group:
                if item not in removed:
                    item.unlink(missing_ok=True)
                    removed.add(item)
                    total -= count
        if incoming > limit:
            raise OSError("Recording exceeds storage limit")

    def _save(self, generation, settings, record, pcm):
        with self.lock:
            try:
                if self.closed or generation != self.generation:
                    return
                private_directory(self.root)
                audio = None
                metadata = None
                if record["kind"] == "speech" and settings.save_training_audio and pcm:
                    if len(pcm) % 2 or len(pcm) > 16000 * 2 * 30:
                        raise ValueError("Invalid training clip")
                    from recording_wav import wav_bytes

                    audio = wav_bytes(pcm)
                    metadata = dict(
                        record,
                        reviewed=False,
                        source="pipewire-playback-monitor",
                        audio_sink=settings.audio_sink,
                        sample_rate=16000,
                        seconds=len(pcm) / 32000,
                        audio_sha256=hashlib.sha256(audio).hexdigest(),
                        hypothesis=" ".join(
                            x.get("source_text", "") for x in record["lines"]
                        ).strip(),
                    )
                logged = (record["kind"] == "speech" and settings.log_speech) or (
                    record["kind"] == "ocr" and settings.log_ocr
                )
                payload = (
                    (json.dumps(record, ensure_ascii=False) + "\n").encode()
                    if logged
                    else b""
                )
                meta = (
                    (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
                    if metadata
                    else b""
                )
                self._prune(settings, len(audio or b"") + len(meta) + len(payload))
                if audio:
                    directory = self.root / "captures"
                    private_directory(directory)
                    path = directory / record["id"]
                    write_private(path.with_suffix(".wav"), audio)
                    try:
                        write_private(path.with_suffix(".json"), meta)
                    except Exception:
                        path.with_suffix(".wav").unlink(missing_ok=True)
                        raise
                if payload:
                    directory = self.root / "transcripts"
                    private_directory(directory)
                    # One event per line; an individual log is also bounded by the global cap.
                    write_private(
                        directory / (record["captured_at"][:10] + ".jsonl"),
                        payload,
                        append=True,
                    )
                self.state = "Saved locally at " + record["captured_at"]
            except (OSError, ValueError, TypeError):
                self.state = "Could not save local recording; check storage/permissions"
            finally:
                self.pending -= 1

    def close(self):
        with self.lock:
            self.closed = True
            self.generation += 1
        self.executor.shutdown(wait=True, cancel_futures=True)
