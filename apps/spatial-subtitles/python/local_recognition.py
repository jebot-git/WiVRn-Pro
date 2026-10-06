# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental OpenVINO recognition. Run in the optional Python 3.12 environment.

All neural inference is explicitly compiled on the selected Intel GPU. There is
no AUTO/HETERO or CPU fallback. Image geometry and colour sampling use NumPy.
"""

from pathlib import Path
import time


def local_root():
    import os

    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "spatial-subtitles"
    )


def select_device(requested="A770"):
    import openvino as ov

    core = ov.Core()
    devices = {
        d: core.get_property(d, "FULL_DEVICE_NAME")
        for d in core.available_devices
        if d.startswith("GPU")
    }
    if requested == "A770":
        matches = [d for d, name in devices.items() if "A770" in name]
        if len(matches) != 1:
            raise RuntimeError(
                "Exactly one Intel Arc A770 is required; select a GPU explicitly otherwise"
            )
        requested = matches[0]
    if requested not in devices:
        raise RuntimeError(
            "Requested OpenVINO GPU is unavailable; CPU fallback is disabled"
        )
    return requested, devices[requested]


class LocalRecognizer:
    def __init__(
        self,
        device="A770",
        root=None,
        download=False,
        speech_model="",
        ocr_model="",
        speaker_model="",
    ):
        self.device, self.device_name = select_device(device)
        self.root = Path(root) if root else local_root()
        self.download = download
        self.speech_model = (
            Path(speech_model).expanduser()
            if speech_model
            else self.root / "models/whisper-large-v3-turbo"
        )
        self.ocr_model = Path(ocr_model).expanduser() if ocr_model else None
        self.speaker_directory = (
            Path(speaker_model).expanduser()
            if speaker_model
            else self.root / "models/speaker-cjk"
        )
        self.speaker = None
        self.whisper = None
        self.ocr = None
        self.recognizers = []
        self.script_router = None
        self.compiled_devices = []

    def load_speech(self):
        if self.whisper is not None:
            return
        import openvino_genai as genai

        model = self.speech_model
        if not (model / "openvino_encoder_model.xml").is_file():
            raise RuntimeError("Local Whisper model missing; run setup.sh")
        cache = self.root / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        self.whisper = genai.WhisperPipeline(
            str(model), self.device, CACHE_DIR=str(cache)
        )

    def speech(self, pcm, language=None, distinguish=False, timestamps=False):
        import numpy as np

        if not pcm or len(pcm) % 2 or len(pcm) > 16000 * 2 * 30:
            raise ValueError("Expected at most 30 seconds of mono 16 kHz PCM16")
        self.load_speech()
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        options = dict(task="transcribe", max_new_tokens=160, do_sample=False)
        if distinguish and timestamps:
            options["return_timestamps"] = True
        if language:
            options["language"] = "<|" + language + "|>"
        start = time.monotonic()
        result = self.whisper.generate(samples, **options)
        response = {
            "text": " ".join(result.texts).strip(),
            "seconds": time.monotonic() - start,
            "device": self.device,
            "device_name": self.device_name,
        }
        if distinguish:
            if self.speaker is None:
                from local_speaker import SpeakerModel

                self.speaker = SpeakerModel(self.speaker_directory, self.device)
            chunks = []
            for chunk in result.chunks or []:
                begin = max(0.0, float(chunk.start_ts))
                end = min(len(samples) / 16000, float(chunk.end_ts))
                if end <= begin:
                    continue
                chunks.append(
                    dict(
                        text=chunk.text,
                        offset=begin,
                        embedding=self.speaker.embed(
                            samples[int(begin * 16000) : int(end * 16000)]
                        ),
                    )
                )
            if not chunks:
                chunks = [
                    dict(
                        text=response["text"],
                        offset=0.0,
                        embedding=self.speaker.embed(samples),
                    )
                ]
            response["segments"] = chunks[:16]
        response["seconds"] = time.monotonic() - start
        return response

    def load_ocr(self):
        if self.ocr is not None:
            return
        import openvino as ov
        from rapidocr import RapidOCR, EngineType, LangRec, ModelType, OCRVersion
        from rapidocr.ch_ppocr_rec import TextRecognizer
        from rapidocr.inference_engine.base import FileInfo
        import rapidocr.inference_engine.openvino as adapter
        from rapidocr.utils.download_file import DownloadFile, DownloadFileInput
        from rapidocr.utils.log import logger
        from copy import deepcopy

        owner = self

        # RapidOCR 3.9.2 hardcodes CPU in its OpenVINO adapter. Replace only the
        # adapter constructor inside this isolated process, retaining upstream
        # model resolution, dictionaries and pre/postprocessing.
        class GPUInferSession(adapter.OpenVINOInferSession):
            def __init__(self, cfg):
                path = cfg.get("model_path")
                if not path:
                    info = self.get_model_url(
                        FileInfo(
                            cfg.engine_type,
                            cfg.ocr_version,
                            cfg.task_type,
                            cfg.lang_type,
                            cfg.model_type,
                        )
                    )
                    path = Path(cfg.model_root_dir) / Path(info["model_dir"]).name
                    if not path.exists():
                        if not owner.download:
                            raise RuntimeError("Local OCR model missing; run setup.sh")
                        path.parent.mkdir(parents=True, exist_ok=True)
                        DownloadFile.run(
                            DownloadFileInput(
                                file_url=info["model_dir"],
                                sha256=info["SHA256"],
                                save_path=path,
                                logger=logger,
                            )
                        )
                core = ov.Core()
                self.model = core.read_model(str(path))
                compiled = core.compile_model(
                    self.model,
                    owner.device,
                    {
                        "PERFORMANCE_HINT": "LATENCY",
                        "CACHE_DIR": str(owner.root / "cache"),
                    },
                )
                actual = compiled.get_property("EXECUTION_DEVICES")
                if not actual or not all(d == owner.device for d in actual):
                    raise RuntimeError("OCR did not compile on the requested GPU")
                owner.compiled_devices.append(list(actual))
                self.session = compiled.create_infer_request()

        adapter.OpenVINOInferSession = GPUInferSession
        params = {
            "Global.model_root_dir": str(self.root / "models/ocr"),
            "Global.log_level": "warning",
            "Global.use_cls": False,
            "Global.text_score": 0.65,
            "Det.limit_side_len": getattr(self, "detector_size", 640),
            "Det.limit_type": "max",
            "Rec.rec_batch_num": 4,
        }
        for stage in ("Det", "Rec", "Cls"):
            params[stage + ".engine_type"] = EngineType.OPENVINO
        # Chinese/Japanese/Latin share v6; Korean and Cyrillic need v5 heads.
        params.update(
            {"Cls.ocr_version": OCRVersion.PPOCRV5, "Cls.model_type": ModelType.MOBILE}
        )
        if self.ocr_model:
            for filename in ("cjk.onnx", "korean.onnx"):
                if not (self.ocr_model / filename).is_file():
                    raise RuntimeError("Incomplete trained OCR export")
            params["Rec.model_path"] = str(self.ocr_model / "cjk.onnx")
        self.ocr = RapidOCR(params=params)
        self.recognizers = [("cjk", self.ocr.text_rec)]
        for name, lang in [
            ("korean", LangRec.KOREAN),
            ("cyrillic", LangRec.CYRILLIC),
            ("thai", LangRec.TH),
        ]:
            cfg = deepcopy(self.ocr.cfg.Rec)
            cfg.ocr_version = OCRVersion.PPOCRV5
            cfg.model_type = ModelType.MOBILE
            cfg.lang_type = lang
            cfg.model_path = (
                str(self.ocr_model / (name + ".onnx"))
                if self.ocr_model and (self.ocr_model / (name + ".onnx")).is_file()
                else None
            )
            self.recognizers.append((name, TextRecognizer(cfg)))
        if self.ocr_model and (self.ocr_model / "script-router.xml").exists():
            self.script_router = ov.Core().compile_model(
                str(self.ocr_model / "script-router.xml"),
                self.device,
                {"PERFORMANCE_HINT": "LATENCY", "CACHE_DIR": str(self.root / "cache")},
            )

    def image(self, rgb):
        import cv2
        import numpy as np
        from rapidocr.ch_ppocr_rec import TextRecInput
        from rapidocr.utils.process_img import get_rotate_crop_image

        self.load_ocr()
        if rgb.ndim != 3 or rgb.shape[2] != 3 or max(rgb.shape[:2]) > 2048:
            raise ValueError(
                "Expected an RGB image no larger than 2048 pixels per side"
            )
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        start = time.monotonic()
        detected = self.ocr(bgr, use_rec=False, use_cls=False)
        boxes = detected.boxes
        if boxes is None or not len(boxes):
            return {
                "regions": [],
                "seconds": time.monotonic() - start,
                "device": self.device,
            }
        # Bound GPU work on crowded scenes; prioritize larger readable text.
        boxes = sorted(
            boxes, key=lambda b: cv2.contourArea(b.astype(np.float32)), reverse=True
        )[: getattr(self, "max_regions", 8)]
        crops = [get_rotate_crop_image(bgr, b.astype(np.float32)) for b in boxes]
        candidates = [[] for _ in crops]
        routes = [None] * len(crops)
        if (
            self.script_router is not None
            and getattr(self, "source_language", "Auto") == "Auto"
        ):
            from PIL import Image

            inputs = []
            for crop in crops:
                im = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))
                im.thumbnail((192, 32))
                canvas = Image.new("L", (192, 32), 128)
                canvas.paste(im, (0, (32 - im.height) // 2))
                inputs.append(np.asarray(canvas, dtype=np.float32)[None] / 127.5 - 1)
            logits = self.script_router([np.stack(inputs)])[0]
            probs = np.exp(logits - logits.max(-1, keepdims=True))
            probs /= probs.sum(-1, keepdims=True)
            labels = ["cjk", "korean", "thai", "cyrillic"]
            routes = [
                labels[int(p.argmax())] if float(p.max()) >= 0.95 else None
                for p in probs
            ]
        for name, recognizer in self.recognizers:
            source = getattr(self, "source_language", "Auto")
            wanted = {
                "Japanese": "cjk",
                "Chinese": "cjk",
                "English": "cjk",
                "Korean": "korean",
                "Thai": "thai",
                "Russian": "cyrillic",
                "Serbian": "cyrillic",
            }.get(source)
            if wanted and name != wanted:
                continue
            indices = [i for i in range(len(crops)) if routes[i] in (None, name)]
            if not indices:
                continue
            result = recognizer(TextRecInput(img=[crops[i] for i in indices]))
            for index, text, score in zip(indices, result.txts, result.scores):
                # A specialist's Latin guesses are not evidence of that script.
                if name == "korean" and not any(
                    "\uac00" <= c <= "\ud7af" for c in text
                ):
                    continue
                if name == "thai" and not any("\u0e00" <= c <= "\u0e7f" for c in text):
                    continue
                if name == "cyrillic" and not any(
                    "\u0400" <= c <= "\u052f" for c in text
                ):
                    continue
                candidates[index].append((float(score), text, name))
        height, width = rgb.shape[:2]
        regions = []
        for box, options in zip(boxes, candidates):
            if not options:
                continue
            score, text, script = max(options)
            if score < 0.7 or not text.strip():
                continue
            x0, y0 = np.floor(np.min(box, axis=0)).astype(int)
            x1, y1 = np.ceil(np.max(box, axis=0)).astype(int)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(width, x1), min(height, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            foreground, background = estimate_colours(rgb[y0:y1, x0:x1])
            regions.append(
                {
                    "text": text.strip()[:240],
                    "confidence": score,
                    "script": script,
                    "box": [
                        x0 / width,
                        y0 / height,
                        (x1 - x0) / width,
                        (y1 - y0) / height,
                    ],
                    "foreground": foreground,
                    "background": background,
                }
            )
        return {
            "regions": regions,
            "seconds": time.monotonic() - start,
            "device": self.device,
            "device_name": self.device_name,
        }


def estimate_colours(crop):
    """Two colour clusters; the dominant cluster is the apparent background."""
    import numpy as np

    pixels = crop.reshape(-1, 3).astype(np.float32)[:: max(1, crop.size // 12000)]
    luminance = pixels @ np.array([0.2126, 0.7152, 0.0722])
    centres = np.array([pixels[luminance.argmin()], pixels[luminance.argmax()]])
    for _ in range(6):
        labels = (
            ((pixels[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
        )
        for i in range(2):
            if np.any(labels == i):
                centres[i] = np.median(pixels[labels == i], axis=0)
    bg = int(np.bincount(labels, minlength=2).argmax())
    return (centres[1 - bg] / 255).tolist(), (centres[bg] / 255).tolist()
