# CJK adaptation pilots

The scripts perform real gradient updates and save candidates outside Git. They
never replace the bundled inference models or change live subtitle settings.
Training uses native PyTorch XPU on the Arc A770; exported inference uses
OpenVINO. IPEX is not required. No CPU fallback is allowed for neural training or
inference. Image/audio preprocessing, export and metrics use the CPU.

## Speech

`setup.sh` creates a separate Python 3.12 training environment and downloads the
pinned OpenAI Whisper large-v3-turbo checkpoint. `prepare_speech.py` downloads
pinned FLEURS Parquet files, extracting 60 training and 12 validation clips per
language (Korean, Mandarin Chinese and Japanese). Clips are 1–25 seconds. Audio
hashes, source IDs, transcripts and attribution are recorded in the manifest.
The official validation split is never used for gradient updates. Full source
shards are several GB even though the pilot uses a small subset.

```sh
fork/local-ai/training/setup.sh
trainroot="$HOME/.local/share/wivrn-pro/training"
"$trainroot/venv/bin/python" fork/local-ai/training/prepare_speech.py --output "$trainroot/fleurs"
"$trainroot/venv/bin/python" fork/local-ai/training/train_speech.py \
  --base "$trainroot/base" --data "$trainroot/fleurs/manifest.json" \
  --output "$trainroot/runs/cjk-pilot-001"
"$trainroot/venv/bin/python" fork/local-ai/training/export_speech.py \
  "$trainroot/runs/cjk-pilot-001" \
  "$HOME/.local/share/wivrn-pro/local-ai/models/whisper-cjk-pilot-001"
```

The pilot updates rank-8 decoder query/value LoRA adapters (327,680 parameters),
using one example from each language per update, 60 updates and a 0.00005 learning
rate. It saves adapter weights, a merged checkpoint and before/after CER. CER
normalizes Unicode, whitespace and punctuation. It is character based because
space-delimited WER is inappropriate for Japanese and Chinese. A small validation
set is useful for smoke testing, not evidence of general improvement. A broader
independent test set and spontaneous/noisy speech are needed before promotion.

## OCR

`train_ocr.py` renders held-out and training FLEURS phrases with Noto Sans CJK in
random contrasting colours and sizes. This is explicitly synthetic sign data,
not photographs or handwriting. It extracts frozen PP-OCRv6 CJK and PP-OCRv5
Korean features with OpenVINO on the A770, then fine-tunes their final CTC linear
heads with PyTorch XPU. The detector, feature backbone, dictionaries and Cyrillic
head remain unchanged. The output contains `cjk.onnx` and `korean.onnx` with
embedded dictionaries, which OpenVINO loads directly.

Install `onnx` and `rapidocr==3.9.2` in the training environment. Download
[Noto Sans CJK Regular](https://github.com/notofonts/noto-cjk/blob/main/Sans/OTC/NotoSansCJK-Regular.ttc)
to the training directory, then run:

```sh
"$trainroot/venv/bin/python" fork/local-ai/training/train_ocr.py \
  --data "$trainroot/fleurs/manifest.json" \
  --models "$HOME/.local/share/wivrn-pro/local-ai/models/ocr" \
  --font "$trainroot/NotoSansCJK-Regular.ttc" \
  --output "$HOME/.local/share/wivrn-pro/local-ai/models/ocr-cjk-pilot-001"
```

The synthetic validation set is easy: original and adapted heads both scored
zero CER. That is not a demonstrated improvement. Keep the bundled OCR selected
until real scenes, oblique signs and handwriting are evaluated.

## WiVRn hooks and results

The Subtitles GUI has separate **Trained speech model** and **Trained OCR heads**
directory selectors. Select a completed candidate and Apply during session to
restart only the local recognition worker. Empty fields restore bundled models.
Never select a directory while export is still writing it. These controls work
with the existing enable/disable toggles and English/Serbian output choices.

The 2026-10-06 pilot's direct OpenVINO evaluation on 12 clips per language:

| Language | Bundled INT8 CER | Adapted FP16 CER |
|---|---:|---:|
| Korean | 8.72% | 9.00% |
| Mandarin Chinese | 1.82% | 1.52% |
| Japanese | 2.80% | 2.61% |

The PyTorch FP32 baseline for Korean was 9.00%, unchanged after training.
Quantization/export differences matter, so the deployment comparison uses the
actual bundled INT8 and candidate FP16 exports. The Korean regression means the
candidate is not automatically selected. VR test clips ran in 0.24–0.60 seconds
on the candidate after initialization. Raw evaluation results and model weights
are saved locally under `training/runs/cjk-pilot-001` and `local-ai/models`.

Sources and licences:

- [Google FLEURS](https://huggingface.co/datasets/google/fleurs), CC-BY-4.0;
  Conneau et al., *FLEURS: Few-shot Learning Evaluation of Universal Representations
  of Speech* (2022). Pinned Parquet revision is recorded in `prepare_speech.py`.
- [OpenAI Whisper](https://huggingface.co/openai/whisper-large-v3-turbo), MIT;
  base checkpoint revision is pinned in setup.
- [RapidOCR](https://github.com/RapidAI/RapidOCR) and
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), Apache-2.0;
  source weight hashes are recorded in the OCR report.
- [Noto CJK](https://github.com/notofonts/noto-cjk), SIL Open Font License 1.1;
  the font hash is recorded in the OCR report.
