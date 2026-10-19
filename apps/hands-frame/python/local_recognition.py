# SPDX-License-Identifier: GPL-3.0-or-later
"""Hands Frame local RapidOCR/PP-OCR inference on a selected GPU."""

from pathlib import Path
import time


def local_root():
    from settings import ROOT
    return ROOT


class LocalRecognizer:
    def __init__(self, device="A770", root=None, download=False, ocr_model="", backend="OpenVINO", device_id=0):
        from gpu_backend import select_ocr_device
        self.backend, self.device_id = backend, device_id
        self.device, self.device_name = select_ocr_device(backend, device, device_id)
        self.root = Path(root) if root else local_root()
        self.download = False
        from settings import MODEL_ROOT
        self.model_root = MODEL_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root/"cache").mkdir(exist_ok=True)
        self.ocr_model = Path(ocr_model).expanduser() if ocr_model else None
        self.ocr = None
        self.recognizers = []
        self.compiled_devices = []

    def load_ocr(self):
        if self.ocr is not None:
            return
        from rapidocr import RapidOCR, EngineType, LangRec, ModelType, OCRVersion
        from rapidocr.ch_ppocr_rec import TextRecognizer
        from copy import deepcopy
        from gpu_backend import install_adapter
        install_adapter(self)
        params = {
            "Global.model_root_dir": str(self.model_root / "ocr"),
            "Global.log_level": "warning",
            "Global.use_cls": False,
            "Global.text_score": 0.65,
            "Det.limit_side_len": getattr(self, "detector_size", 640),
            "Det.limit_type": "max",
            "Rec.rec_batch_num": 4,
        }
        for stage in ("Det", "Rec", "Cls"):
            params[stage + ".engine_type"] = EngineType.OPENVINO if self.backend == "OpenVINO" else EngineType.ONNXRUNTIME
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
            indices = list(range(len(crops)))
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
