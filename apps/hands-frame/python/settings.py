# SPDX-License-Identifier: GPL-3.0-or-later
"""Hands Frame OCR settings. Translation credentials are stored separately."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json, os, stat, tempfile

ROOT = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'wivrn-pro/hands-frame'
PACKAGED_MODELS = Path(__file__).resolve().parent.parent/'models'
MODEL_ROOT = PACKAGED_MODELS if PACKAGED_MODELS.is_dir() else ROOT/'models'
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'wivrn-pro/hands-frame.json'
PROVIDERS = ('DeepL API Free', 'Google Cloud Translation')
BACKENDS = ('OpenVINO', 'CUDA', 'ROCm')
LANGUAGES = ('Auto','Japanese','Korean','Chinese','Thai','English','Serbian','Russian')


def private_json(path, limit=32768):
    fd = os.open(path, os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode&0o077 or info.st_size>limit:
            raise ValueError('Hands Frame settings and keys must be private files (mode 0600)')
        return json.load(stream)


def atomic_json(path, value):
    path = Path(path);path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.hands-frame-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class Settings:
    translation_provider: str = PROVIDERS[0]
    ocr_backend: str = 'OpenVINO'
    local_device: str = 'A770'
    gpu_device_id: int = 0
    local_ocr_model: str = str(MODEL_ROOT/'ocr-trained')
    source_language: str = 'Auto'
    ocr_max_regions: int = 8
    ocr_detector_size: int = 640

    @classmethod
    def parse(cls, value):
        if not isinstance(value,dict) or set(value)-set(cls.__dataclass_fields__):raise ValueError('Unknown Hands Frame settings')
        s=cls(**value)
        if s.translation_provider not in PROVIDERS:raise ValueError('Choose Google Cloud Translation or DeepL API Free')
        if s.ocr_backend not in BACKENDS:raise ValueError('Choose OpenVINO, CUDA or ROCm for OCR')
        if s.source_language not in LANGUAGES:raise ValueError('Unsupported source language')
        if s.local_device not in ('A770','GPU.0','GPU.1','GPU.2','GPU.3'):raise ValueError('Select an OpenVINO GPU')
        for name, low, high in [('gpu_device_id',0,31),('ocr_max_regions',1,24),('ocr_detector_size',320,960)]:
            v=getattr(s,name)
            if type(v) is not int or not low<=v<=high:raise ValueError('Invalid '+name)
        if not isinstance(s.local_ocr_model,str) or len(s.local_ocr_model)>4096 or any(ord(c)<32 for c in s.local_ocr_model) or s.local_ocr_model and not Path(s.local_ocr_model).expanduser().is_absolute():raise ValueError('Use an absolute OCR model directory')
        return s

    @classmethod
    def load(cls):
        try:return cls.parse(private_json(CONFIG))
        except FileNotFoundError:return cls()

    def save(self):atomic_json(CONFIG,asdict(self))
