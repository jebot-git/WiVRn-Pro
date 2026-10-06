# SPDX-License-Identifier: GPL-3.0-or-later
"""Local recognition with explicitly selectable local or DeepL Free translation."""

from dataclasses import dataclass, asdict
import json, math, os, tempfile
from pathlib import Path

ROOT = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    / "spatial-subtitles"
)
CONFIG = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "spatial-subtitles/settings.json"
)
RUNTIME = (
    Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    / "spatial-subtitles"
)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    ocr_enabled: bool = True
    language: str = "English"
    ocr_language: str = "English"
    source_language: str = "Auto"
    translation_provider: str = "Local OpenVINO"
    local_device: str = "A770"
    local_speech_model: str = str(ROOT / "models/whisper-large-v3-turbo")
    local_ocr_model: str = ""
    local_speaker_model: str = str(ROOT / "models/speaker-cjk")
    local_translation_model: str = str(ROOT / "models/translation-selected-fp16")
    local_speaker_threshold: float = 0.4
    local_ocr_confidence: float = 0.9
    distinguish_voices: bool = True
    split_voices_within_chunk: bool = False
    spatial: bool = True
    audio_sink: str = "@DEFAULT_AUDIO_SINK@"
    font: str = "Jupiteroid"
    duration: float = 5.0
    scale: float = 1.0
    segment_seconds: float = 3.0
    silence_seconds: float = 0.5
    threshold: float = 0.012
    ocr_interval: float = 3.0
    ocr_duration: float = 3.0
    ocr_distance: float = 2.0
    ocr_max_regions: int = 8
    ocr_detector_size: int = 640
    save_training_audio: bool = False
    log_speech: bool = False
    log_ocr: bool = False
    recording_retention_days: int = 7
    recording_max_mb: int = 512

    @classmethod
    def parse(cls, d):
        if not isinstance(d, dict) or set(d) - set(cls.__dataclass_fields__):
            raise ValueError("Unknown settings")
        s = cls(**d)
        for k, v in asdict(s).items():
            default = getattr(cls(), k)
            if type(default) == bool and type(v) != bool:
                raise ValueError(k + " must be boolean")
            if type(default) == str and (
                not isinstance(v, str) or len(v) > 4096 or any(ord(c) < 32 for c in v)
            ):
                raise ValueError("Invalid " + k)
        for k in ["language", "ocr_language"]:
            if getattr(s, k) not in ["English", "Serbian", "Original"]:
                raise ValueError("Unsupported output language")
        if s.source_language not in [
            "Auto",
            "Japanese",
            "Korean",
            "Chinese",
            "Thai",
            "English",
            "Serbian",
            "Russian",
        ]:
            raise ValueError("Unsupported source language")
        if s.translation_provider not in ["Local OpenVINO", "DeepL API Free"]:
            raise ValueError("Unsupported translation provider")
        if s.local_device not in ["A770", "GPU.0", "GPU.1", "GPU.2", "GPU.3"]:
            raise ValueError("Select an Intel GPU")
        for k in [
            "local_speech_model",
            "local_ocr_model",
            "local_speaker_model",
            "local_translation_model",
        ]:
            v = getattr(s, k)
            if v and not Path(v).expanduser().is_absolute():
                raise ValueError("Use absolute model directories")
        for k, lo, hi in [
            ("duration", 2, 10),
            ("scale", 0.6, 1.5),
            ("segment_seconds", 1, 5),
            ("silence_seconds", 0.2, 1.5),
            ("threshold", 0.001, 0.2),
            ("ocr_interval", 1, 30),
            ("ocr_duration", 1, 10),
            ("ocr_distance", 0.5, 10),
            ("local_ocr_confidence", 0.5, 1),
            ("local_speaker_threshold", 0.3, 0.95),
            ("recording_retention_days", 1, 365),
            ("recording_max_mb", 32, 8192),
            ("ocr_max_regions", 1, 24),
            ("ocr_detector_size", 320, 960),
        ]:
            v = getattr(s, k)
            if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError("Out of range: " + k)
            if type(getattr(cls(), k)) == int and type(v) != int:
                raise ValueError("Integer required: " + k)
        if not s.audio_sink or s.audio_sink.startswith("-") or len(s.audio_sink) > 256:
            raise ValueError("Invalid audio sink")
        return s

    @classmethod
    def load(cls):
        return cls.parse(json.loads(CONFIG.read_text())) if CONFIG.exists() else cls()
