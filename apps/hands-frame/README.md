# Optional Hands Frame OCR package

This package adds text recognition and translation to WiVRn-Pro Hands Frame.
It installs independently of the main server and Android client. Without it,
Hands Frame photo, silent video and barcode scanning remain available; OCR
translation is marked unavailable. There is no speech recognition, microphone
capture, subtitle overlay, local translation model or automatic model download.

OCR uses RapidOCR 3.9.2 / PP-OCRv6 detection and CJK recognition with PP-OCRv5
Korean, Cyrillic and Thai specialist heads. Trained recognition heads can be
provided. Images stay on the PC. Choose one OCR GPU backend:

- OpenVINO: explicitly selected Intel GPU.
- CUDA: ONNX Runtime CUDA execution provider, for NVIDIA GPUs.
- ROCm: ONNX Runtime MIGraphX (or legacy ROCm execution provider), for AMD GPUs.

A selected provider must initialize successfully. CPU fallback is disabled.
CUDA/ROCm options do not change WiVRn's video encoder/GPU policy.

Translation providers are **Google Cloud Translation Basic v2** and **DeepL API
Free**. Only recognized text is sent over HTTPS. Credentials are entered in
**Hands Frame → OCR settings** and stored in separate private files under
`~/.config/wivrn-pro/`. Output language **Original** performs OCR without an API
call. No provider is substituted silently on failure.

## Build/install the optional package

Python 3.12 and `uv` are needed to prepare a runtime. The settings window uses the
system PySide6 package. Pass a directory containing the PP-OCR ONNX model files;
the runtime never fetches weights. For example:

```sh
python apps/hands-frame/install.py --backend openvino --models /path/to/ocr \
  --trained-models /path/to/trained-heads --prefix "$HOME/.local/opt/wivrn-pro"
python apps/hands-frame/build-package.py --output build/wivrn-pro-hands-frame-ocr-openvino.tar.gz
```

The CUDA variant uses `--backend cuda`. Install the NVIDIA CUDA/cuDNN versions
required by [ONNX Runtime CUDA](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).
ROCm uses `--backend rocm --rocm-wheel URL_OR_PATH` with the wheel matching the
installed ROCm release, available from [AMD's official ONNX Runtime instructions](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/onnxruntime.html).
The adapter also registers the newer `onnxruntime_ep_migraphx` plugin when present.
Vendor GPU drivers are system prerequisites and are not included in the package.

Extract the resulting archive into the WiVRn-Pro prefix. It contains only
`libexec/wivrn-pro/hands-frame-ocr/` and the settings launcher, so it does not
replace server/client binaries. Reopen Hands Frame after adding/removing it.
Remove that directory and `bin/wivrn-pro-hands-frame-settings` to uninstall;
settings and saved captures remain. Package variants provide one dependency
runtime at a time; choose the appropriate variant for the selected OCR GPU.

The installer supports `--migrate-legacy` to copy the former standalone OCR
settings and DeepL key. It leaves old model and configuration files intact.
No speech or local translation weights are copied. OCR application sources are
GPL-3.0-or-later; RapidOCR and PP-OCR retain their upstream Apache-2.0 notices.
API adapters follow [Google's Basic API](https://docs.cloud.google.com/translate/docs/reference/rest/v2/translate)
and the DeepL API Free text interface.
