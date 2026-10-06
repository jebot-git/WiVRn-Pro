# Experimental local recognition

Install with `fork/local-ai/setup.sh` (requires `uv`, network access and an Intel
GPU driver). Models and the Python 3.12 environment live outside the checkout in
`$XDG_DATA_HOME/wivrn-pro/local-ai`, normally `~/.local/share/wivrn-pro/local-ai`.
The setup pins Whisper large-v3-turbo INT8 OpenVINO weights to a repository
revision. RapidOCR's pinned package resolves OCR weights with SHA-256 checks.
Model licences are supplied by their upstream repositories/packages.

Select **Local OpenVINO** in the unified dashboard's Subtitles tab. `A770`
selects the Arc by its device name, avoiding the integrated GPU even when it is
`GPU.0`. An explicit GPU can also be selected. Neural inference fails closed
without that GPU; CPU fallback is disabled. Geometry, colour sampling and other
pre/postprocessing still run on the CPU. Voice identification is currently only
available with cloud recognition; switching to local disables voice colours.

Speech uses multilingual Whisper large-v3-turbo; OCR uses PP-OCRv6 CJK/Latin and
PP-OCRv5 Korean/Cyrillic heads. Detector rectangles and estimated foreground and
background colours are retained for the existing OpenXR overlays. A confidence
threshold of 0.90 skips many distant or unreadable labels. This does not guarantee
that accepted text is correct. Handwriting and overlapping voices remain limited.

Recognition runs in an isolated persistent process over a private socket. Raw
media never goes to the text translator. **Original** output is fully local.
English and Serbian translation use either the configured OpenAI text API or an
experimental `codex exec` bridge to the local ChatGPT login (`codex login`). The
bridge ignores user configuration, uses an empty directory and a read-only
sandbox, disables shell execution and web search, asks for no tools, and has a
nine-second deadline. It consumes ChatGPT
plan usage and is not a low-latency streaming API. Only recognized text leaves
the PC. Provider errors and stale results are discarded, not queued indefinitely.

Run the hardware check without starting VR:

```sh
~/.local/share/wivrn-pro/local-ai/venv/bin/python fork/local-ai/doctor.py \
  --wav fork/translation-lab/assets/audio/en_us.wav \
  --wav fork/translation-lab/assets/audio/ja_jp.wav \
  --wav fork/translation-lab/assets/audio/ko_kr.wav
```

Add `--image path.png` for OCR; two iterations separate first-inference shader
compilation from warm latency. The private worker log is under `local-ai/worker.log`.

Observed on an Arc A770: model initialization about 5–15 seconds, transcription
of 10–13-second clips about 0.24–0.63 seconds, warm scene OCR about 0.17 seconds.
These exclude capture/chunking and text translation. The test room's large Korean
and Chinese panels were exact; distant Japanese and Russian panels had errors and
fell below the default threshold. Short live chunks can be less accurate than
full clips. These are prototype measurements, not an accuracy benchmark.

## Trained model hook

The **Trained speech model (OpenVINO directory)** field accepts an absolute path
to a completed Whisper export. Leave it empty to restore the bundled model.
Applying a new path cancels old results and restarts the private GPU worker with
that model, without restarting WiVRn. Use a new immutable directory for each
candidate; never point at a checkpoint directory being written during training.
Only exports compatible with OpenVINO GenAI Whisper are supported (encoder,
decoder, configuration and tokenizer files). Missing/incompatible models fail
closed. Training does not automatically replace the active model or change
microphone keepalive. See `training/` for the adaptation pilot and evaluation.

The **Trained OCR heads** field accepts a directory containing compatible
`cjk.onnx` and `korean.onnx` models with their original embedded dictionaries.
Leave it empty to use the bundled heads. The detector and Cyrillic head remain
bundled. Use only complete exports; applying either model selector restarts the
shared local recognition worker and discards results from the old configuration.

## Optional recordings and training from sessions

The Subtitles tab has three independent, default-off controls:

- Save recognized audio clips for training.
- Save speech transcripts and translations.
- Save scene-text transcripts and translations.

They take effect when Apply is pressed. Audio collection saves recognized chunks
of the existing **WiVRn speaker mix**, not the headset microphone. It requires
speech subtitles to be enabled and recognition/translation to finish successfully.
It is not a continuous audio recorder. OCR logs require scene translation to be
enabled; no scene images are written. Each UTC-stamped JSONL log event includes
original text, displayed translation, output language and available speaker/box
metadata. Identical OCR text is logged at most once per 30 seconds. Recognized
text can contain errors.

Files are private to the current user (directories 0700, files 0600), under
`~/.local/share/wivrn-pro/recordings` or the equivalent XDG data directory.
The GUI can open this folder. `captures/` holds mono 16 kHz PCM16 WAVs and JSON
sidecars. `transcripts/` holds daily JSONL logs. The default limit is 512 MiB and
7 days; both are configurable. Old files are removed as new records are saved,
including unexported reviewed clips. Disabled recording writes nothing new and
discards queued saves. Storage errors do not stop subtitles. A bounded queue
skips new records if the writer cannot keep up.

Training hypotheses are explicitly **unreviewed**, not ground truth. Listen to a
WAV, correct its original-language transcript, and approve its spoken language:

```sh
python3 fork/local-ai/training/review_captures.py list
python3 fork/local-ai/training/review_captures.py approve CAPTURE_ID \
  --language ja --text 'Corrected Japanese transcript here'
python3 fork/local-ai/training/review_captures.py export /path/to/reviewed.json
```

Export copies approved WAVs into a separate private dataset directory, outside
recording retention. Add `--captures /path/to/reviewed.json` to `train_speech.py`
to include those examples in training. Hash checks detect modified audio; only
reviewed training rows are accepted. Public validation clips remain held out.
Languages in the reviewed dataset join the balanced training batches. The
existing export and GUI model-selection steps remain required; recording never
silently retrains or replaces the running model.
