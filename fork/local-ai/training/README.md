# CJK adaptation pilots

These are historical WiVRn integration experiments. The active application and
new four-language training live in [Spatial Subtitles](../../../apps/spatial-subtitles/README.md);
its inference is fully local, and the old dashboard/provider controls no longer exist.

The training scripts perform real gradient updates and save candidates outside Git. They
never replace the bundled inference models or change live subtitle settings.
Training uses native PyTorch XPU on the Arc A770; exported inference uses
OpenVINO. IPEX is not required. No CPU fallback is allowed for neural training or
inference. Image/audio preprocessing, export and metrics use the CPU.

## Local voice distinction

`prepare_speakers.py`, `prepare_koniwa.py`, `merge_speaker_corpora.py` and
`train_speakers.py` build and train a separate speaker metric. This does not
fine-tune Whisper or change its word recognition. The pinned WeSpeaker ResNet34
backbone runs through OpenVINO on the A770; native PyTorch XPU trains a 65,536
parameter linear metric initialized to the identity. Clean and noisy/reverberant
views are paired using dataset speaker labels. Exported inference uses OpenVINO
for both the backbone and the trained metric. No CPU neural fallback is used.

The 2026-10-06 corpus contains **12.7046 hours of unique training audio**,
1.5007 validation hours and 1.5010 test hours. There are 16,201 non-overlapping
PCM windows and 689 source-scoped speaker labels (566 train, 61 validation,
62 test). Identical PCM hashes are deduplicated. Augmented views and repeated
epochs do not count towards the hours. Every training window is consumed.

| Training language/source | Hours | Documented source types |
|---|---:|---|
| Japanese, Emilia | 4.0004 | Online speech; per-clip genre unspecified |
| Korean, Emilia | 4.0006 | Online speech; per-clip genre unspecified |
| Chinese, CN-Celeb | 4.0004 | Interviews, entertainment, broadcasts, speeches, vlogs |
| Japanese, Koniwa/LibriVox | 0.5000 | Audiobooks, seven reader labels |
| Japanese, Koniwa | 0.2033 | FM Amagasaki and J-WAVE radio/talk-show speech |

Emilia describes podcasts, talk shows and audiobooks among its source types,
but its downloaded per-clip metadata does not prove a podcast-specific duration
in this subset. This is a mixed multilingual corpus, not a balanced matrix of
every genre in each language. Koniwa provides explicit audiobook/radio provenance.
Only manually transcribed radio spans were considered, and windows with an
unstable voice between their two halves were rejected. Their anonymous speaker
labels are automatically clustered; they are not human-verified diarization.
Emilia labels are also automatic. Transcript-script checks exclude obvious
language mislabelling, and Emilia clips require DNSMOS >= 3.0.

Splits use speaker IDs, not random windows. These are source-scoped IDs: the
same real person might appear under different source labels. Validation chooses
the checkpoint; test pairs are not used for gradient updates. Reported equal
error rates (EER) compare same/different speakers on different source utterances,
not word recognition accuracy or overlapping-speaker diarization error rate.
The small Chinese-only pilot regressed and was not selected for deployment.

The 30-epoch multilingual gradient run also regressed on all three test sets,
despite improving some validation scores. Its checkpoint remains a candidate,
not the active model. The selected model instead uses **statistical speaker
calibration**: a mean embedding is fitted on the full training corpus, balancing
the three languages. Validation selects the centring strength; the pretrained
backbone stays frozen. `calibrate_speakers.py` fits this on XPU and exports the
centering transform for OpenVINO. This is not a claim of successful backbone
fine-tuning or improved word recognition.

| Test language | Pretrained EER | Selected OpenVINO calibration EER |
|---|---:|---:|
| Japanese | 3.13% | 2.39% |
| Korean | 5.80% | 5.27% |
| Chinese | 12.91% | 11.18% |

The selected live similarity threshold is 0.400, chosen from validation negative
pairs to limit false matches to at most approximately 1% per language, with a
conservative 0.4 floor because live phrases can average multiple windows. The tracker also
requires a gap between the best two matches and leaves borderline cases neutral.
These operating-point estimates depend on the source labels and are not a
guarantee for arbitrary game audio. The reference reports are in `reports/`.

Example preparation (download archives to `speakerroot` first):

```sh
speakerroot="$HOME/.local/share/wivrn-pro/training/speakers"
trainer="$HOME/.local/share/wivrn-pro/training/venv/bin/python"
"$trainer" fork/local-ai/training/prepare_speakers.py \
  --output "$speakerroot/corpus" --cn-celeb "$speakerroot/cn-celeb_v2.tar.gz"
"$trainer" fork/local-ai/training/prepare_speakers.py \
  --output "$speakerroot/cjk-ja" --emilia ja --token-file /private/path/hf.token
"$trainer" fork/local-ai/training/prepare_speakers.py \
  --output "$speakerroot/cjk-ko" --emilia ko --token-file /private/path/hf.token
"$trainer" fork/local-ai/training/prepare_koniwa.py \
  --root "$speakerroot" --output "$speakerroot/koniwa-corpus"
"$trainer" fork/local-ai/training/merge_speaker_corpora.py \
  --output "$speakerroot/cjk-mixed/manifest.json" \
  "$speakerroot/corpus/manifest.json" "$speakerroot/cjk-ja/manifest.json" \
  "$speakerroot/cjk-ko/manifest.json" "$speakerroot/koniwa-corpus/manifest.json"
"$trainer" fork/local-ai/training/train_speakers.py \
  --data "$speakerroot/cjk-mixed/manifest.json" --base "$speakerroot/base" \
  --output "$speakerroot/runs/cjk-speaker-001"
"$trainer" fork/local-ai/training/calibrate_speakers.py \
  --data "$speakerroot/cjk-mixed/manifest.json" \
  --embeddings "$speakerroot/runs/cjk-speaker-001/embeddings.npz" \
  --base "$speakerroot/base" \
  --output "$HOME/.local/share/wivrn-pro/local-ai/models/speaker-cjk-20261006"
```

Base directory requires `speaker.onnx`, renamed from the pinned WeSpeaker ONNX
download. `fork/local-ai/setup.sh` installs that backbone under the inference
runtime's `models/speaker-cjk` directory. Koniwa preparation expects its metadata
checkout at `speakerroot/koniwa` and `librivox.zip`, `amagasaki.zip` and
`free_culture_2012.zip` from the repository's public Drive link. Detailed source
archive hashes and the full audio manifest remain in `speakerroot/cjk-mixed`.

In the unified Subtitles GUI, enable **Distinguish voices with labels and
colours** and select a completed **Local voice model directory**. It contains
`speaker.onnx` and optionally `projection.xml`/`.bin`. The similarity threshold
is adjustable without restarting VR. Up to four anonymous voices are remembered
in RAM for the session; applying settings resets them. Short or ambiguous phrases
use the neutral colour. The dominant voice of each timestamped Whisper phrase
is estimated; overlapping voices are not separated. Embeddings are neither
written to transcript logs nor sent to a cloud translation provider or headset.

Sources and use conditions:

- [WeSpeaker ResNet34-LM](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM),
  CC-BY-4.0, revision `f0c48c298fd835726c27956a5d617bad7115627e`.
- [Emilia](https://huggingface.co/datasets/amphion/Emilia-Dataset), original
  Emilia subset, CC-BY-NC-4.0, revision
  `d7f2f7340a6385696f3766c8049fa920a4707c07`; requires account-owner acceptance
  of access terms. This candidate is for local non-commercial experimentation;
  its weights and source audio are not bundled in the Meta APK or Git repository.
- [CN-Celeb](https://www.openslr.org/82/), corrected CN-Celeb1 v2, CC-BY-SA-4.0;
  CSLT, Tsinghua University, Fan et al., ICASSP 2020.
- [Koniwa](https://github.com/koniwa/koniwa): LibriVox public-domain audiobooks;
  FM Amagasaki, City of Amagasaki, CC-BY-4.0; J-WAVE *360° Forum*, CC-BY-SA-3.0
  per the archive README (stricter than its JSON metadata's BY-3.0).
  Reader attribution is retained in the source metadata and local manifest.

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
