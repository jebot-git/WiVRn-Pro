#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local speech/OCR daemon with selectable text translation."""

import os, faulthandler

faulthandler.enable()

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import json, select, signal, subprocess, time
from threading import Event
from settings import Settings, ROOT, RUNTIME, atomic_json
from audio import Segmenter, FRAME_BYTES, direction
from local_recognition import LocalRecognizer
from local_speaker import SpeakerTracker
from subtitle_recording import Recorder
from translation import Translator
from deepl_translation import DeepLTranslator, key_stamp
import capture


def pose_at(captured):
    try:
        pose = json.loads((RUNTIME / "pose.json").read_text())
        history = pose.get("history", [pose])
        match = min(history, key=lambda p: abs(p["time"] - captured))
        return match if abs(match["time"] - captured) < 0.2 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Pipeline:
    def __init__(self, s):
        self.s = s
        self.cancelled = Event()
        self.rec = LocalRecognizer(
            s.local_device,
            root=ROOT,
            speech_model=s.local_speech_model,
            ocr_model=s.local_ocr_model,
            speaker_model=s.local_speaker_model,
        )
        self.rec.source_language = s.source_language
        self.rec.max_regions = s.ocr_max_regions
        self.rec.detector_size = s.ocr_detector_size
        self.translator = None
        self.tracker = SpeakerTracker(s.local_speaker_threshold)

    def warmup(self):
        if self.s.enabled:
            self.rec.load_speech()
        if self.s.ocr_enabled:
            self.rec.load_ocr()
        if (self.s.enabled and self.s.language != "Original") or (
            self.s.ocr_enabled and self.s.ocr_language != "Original"
        ):
            self.translator = self.make_translator()
        return []

    def make_translator(self):
        if self.s.translation_provider == "DeepL API Free":
            return DeepLTranslator(cancelled=self.cancelled)
        return Translator(
            self.s.local_translation_model, self.rec.device, ROOT / "cache"
        )

    def translate_many(self, texts, target):
        if self.cancelled.is_set():
            raise RuntimeError("Translation settings changed")
        if target == "Original" or not texts:
            return texts
        if self.translator is None:
            self.translator = self.make_translator()
        if hasattr(self.translator, "translate_many"):
            return self.translator.translate_many(texts, target, self.s.source_language)
        return [
            self.translator.translate(t, target, self.s.source_language) for t in texts
        ]

    def translate(self, text, target):
        if target == "Original":
            return text
        if self.translator is None:
            self.translator = self.make_translator()
        return self.translator.translate(text, target, self.s.source_language)

    def speech(self, segment):
        languages = {
            "Japanese": "ja",
            "Korean": "ko",
            "Chinese": "zh",
            "Thai": "th",
            "English": "en",
            "Serbian": "sr",
            "Russian": "ru",
        }
        result = self.rec.speech(
            segment.pcm,
            language=languages.get(self.s.source_language),
            distinguish=self.s.distinguish_voices,
            timestamps=self.s.split_voices_within_chunk,
        )
        bearing, spatial = direction(segment.pans, self.s.spatial)
        lines = []
        parts = [
            p
            for p in (result.get("segments") or [dict(text=result["text"])])
            if p["text"].strip()
        ]
        translated = self.translate_many(
            [p["text"].strip() for p in parts], self.s.language
        )
        for part, text in zip(parts, translated):
            source = part["text"].strip()
            if not source:
                continue
            lines.append(
                dict(
                    text=text,
                    source_text=source,
                    speaker=self.tracker.assign(part.get("embedding"))
                    if self.s.distinguish_voices
                    else 0,
                    bearing=bearing if spatial else 0,
                    offset=part.get("offset", 0),
                    anchor=segment.anchor,
                )
            )
        return lines

    def image(self, frame):
        result = self.rec.image(frame["image"])
        lines = []
        regions = [
            r
            for r in result["regions"][: self.s.ocr_max_regions]
            if r["confidence"] >= self.s.local_ocr_confidence
        ]
        translated = self.translate_many(
            [r["text"] for r in regions], self.s.ocr_language
        )
        for r, text in zip(regions, translated):
            lines.append(
                dict(
                    r,
                    source_text=r["text"],
                    text=text,
                )
            )
        return lines


class Daemon:
    def __init__(self):
        self.stop = False
        self.settings = None
        self.key_version = 0
        self.pipe = None
        self.audio = None
        self.buffer = bytearray()
        self.queue = deque(maxlen=2)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.generation = 0
        self.next_ocr = 0
        self.next_config = 0
        self.retry = 0
        self.audio_retry = 0
        self.audio_frames = 0
        self.speech_segments = 0
        self.speech_results = 0
        self.recorder = Recorder()
        self.speech = []
        self.ocr = []
        self.state = "Starting local models"
        self.last_frame = None

    def close_audio(self):
        if self.audio:
            self.audio.terminate()
            try:
                self.audio.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.audio.kill()
                self.audio.wait()
            self.audio.stdout.close()
            self.audio = None
        self.buffer.clear()
        self.queue.clear()

    def reload(self):
        s = Settings.load()
        version = key_stamp() if s.translation_provider == "DeepL API Free" else 0
        if s == self.settings and version == self.key_version:
            return
        self.generation += 1
        if self.pipe is not None:
            self.pipe.cancelled.set()
        self.close_audio()
        self.settings = s
        self.key_version = version
        self.segmenter = Segmenter(s)
        self.pipe = None
        self.speech = []
        self.ocr = []
        self.retry = 0
        self.audio_retry = 0
        self.recorder.configure(s)
        self.state = "Settings applied; recognition stays local"

    def run(self):
        RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
        ROOT.mkdir(parents=True, exist_ok=True)
        try:
            while not self.stop:
                now = time.monotonic()
                if now >= self.next_config:
                    try:
                        self.reload()
                    except Exception as e:
                        self.state = "Settings error: " + str(e)
                    lease = RUNTIME / "capture.request"
                    if self.settings and self.settings.ocr_enabled:
                        atomic_json(lease, 1)
                    else:
                        lease.unlink(missing_ok=True)
                    self.next_config = now + 1
                s = self.settings
                if s is None:
                    time.sleep(0.1)
                    continue
                if s.enabled and self.audio is None and now >= self.audio_retry:
                    # PipeWire's Pulse monitor also works for virtual sinks that
                    # expose no native monitor ports (including WiVRn).
                    monitor = (
                        "@DEFAULT_MONITOR@"
                        if s.audio_sink == "@DEFAULT_AUDIO_SINK@"
                        else s.audio_sink + ".monitor"
                    )
                    self.audio = subprocess.Popen(
                        [
                            "parec",
                            "--device=" + monitor,
                            "--rate=16000",
                            "--channels=2",
                            "--channel-map=front-left,front-right",
                            "--format=s16le",
                            "--raw",
                            "--latency-msec=20",
                            "--client-name=Spatial Subtitles",
                            "--property=node.dont-reconnect=true",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=None,
                    )
                    os.set_blocking(self.audio.stdout.fileno(), False)
                if self.audio and self.audio.poll() is not None:
                    self.close_audio()
                    self.audio_retry = now + 3
                    self.state = "Waiting for selected playback sink"
                if self.future and self.future[0].done():
                    future, gen, kind, item, settings = self.future
                    self.future = None
                    try:
                        lines = future.result()
                        if gen == self.generation and kind == "warmup":
                            self.state = "Recognition models and translator ready"
                        elif gen == self.generation:
                            captured = (
                                item.captured if kind == "speech" else item["captured"]
                            )
                            if now - captured < 12:
                                self.recorder.record(
                                    kind,
                                    lines,
                                    captured,
                                    item.pcm if kind == "speech" else None,
                                )
                                if kind == "speech":
                                    self.speech_results += len(lines)
                                    self.speech = [
                                        dict(x, expires=now + s.duration) for x in lines
                                    ][-4:]
                                else:
                                    self.ocr = [
                                        dict(
                                            x,
                                            expires=now + s.ocr_duration,
                                            fov=item["fov"],
                                            anchor=item.get("anchor"),
                                            distance=s.ocr_distance,
                                        )
                                        for x in lines
                                    ]
                                self.state = (
                                    "Listening — "
                                    + s.translation_provider
                                    + " translation"
                                )
                            else:
                                self.state = "Inference exceeded 12 seconds; stale result skipped"
                    except Exception as e:
                        if gen == self.generation:
                            self.state = (
                                "Recognition/translation error: " + str(e)[:220]
                            )
                            self.retry = now + 5
                if (
                    not self.future
                    and now >= self.retry
                    and (s.enabled or s.ocr_enabled)
                ):
                    if self.pipe is None:
                        self.pipe = Pipeline(s)
                        self.future = (
                            self.executor.submit(self.pipe.warmup),
                            self.generation,
                            "warmup",
                            None,
                            s,
                        )
                        self.state = "Loading local OpenVINO models"
                    if not self.future and self.queue:
                        segment = self.queue.popleft()
                        if now - segment.captured < 8:
                            self.future = (
                                self.executor.submit(self.pipe.speech, segment),
                                self.generation,
                                "speech",
                                segment,
                                s,
                            )
                    elif not self.future and s.ocr_enabled and now >= self.next_ocr:
                        self.next_ocr = now + s.ocr_interval
                        frame = capture.latest()
                        if (
                            frame
                            and (frame["source_pid"], frame["sequence"])
                            != self.last_frame
                        ):
                            self.last_frame = (frame["source_pid"], frame["sequence"])
                            frame["anchor"] = pose_at(frame["captured"])
                            self.future = (
                                self.executor.submit(self.pipe.image, frame),
                                self.generation,
                                "ocr",
                                frame,
                                s,
                            )
                        elif not frame and not s.enabled:
                            self.state = (
                                "Waiting for an application launched with scene capture"
                            )
                atomic_json(
                    RUNTIME / "overlay.json",
                    dict(
                        updated=now,
                        scale=s.scale,
                        font=s.font,
                        speech=[x for x in self.speech if x["expires"] > now]
                        if s.enabled
                        else [],
                        ocr=[x for x in self.ocr if x["expires"] > now]
                        if s.ocr_enabled
                        else [],
                    ),
                )
                atomic_json(
                    RUNTIME / "status.json",
                    dict(
                        updated=time.time(),
                        state=self.state if s.enabled or s.ocr_enabled else "Disabled",
                        pid=os.getpid(),
                        audio_connected=bool(self.audio and self.audio.poll() is None),
                        audio_frames=self.audio_frames,
                        speech_segments=self.speech_segments,
                        speech_results=self.speech_results,
                        inference=self.future[2] if self.future else None,
                    ),
                )
                if self.audio:
                    ready, _, _ = select.select([self.audio.stdout], [], [], 0.05)
                    if ready:
                        chunk = os.read(self.audio.stdout.fileno(), FRAME_BYTES * 25)
                        if not chunk:
                            self.close_audio()
                            self.audio_retry = now + 3
                            self.state = "Waiting for selected playback sink"
                            continue
                        self.buffer.extend(chunk)
                        while len(self.buffer) >= FRAME_BYTES:
                            frame = bytes(self.buffer[:FRAME_BYTES])
                            del self.buffer[:FRAME_BYTES]
                            self.audio_frames += 1
                            segment = self.segmenter.feed(
                                frame, time.monotonic() - len(self.buffer) / 64000
                            )
                            if segment:
                                self.speech_segments += 1
                                segment.anchor = pose_at(segment.captured)
                                self.queue.append(segment)
                else:
                    time.sleep(0.05)
        finally:
            if self.pipe is not None:
                self.pipe.cancelled.set()
            (RUNTIME / "capture.request").unlink(missing_ok=True)
            self.close_audio()
            self.recorder.close()
            self.executor.shutdown(wait=True, cancel_futures=True)
            atomic_json(
                RUNTIME / "overlay.json",
                dict(updated=time.monotonic(), speech=[], ocr=[]),
            )


if __name__ == "__main__":
    daemon = Daemon()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: setattr(daemon, "stop", True))
    daemon.run()
