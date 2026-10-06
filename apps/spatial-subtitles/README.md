# Spatial Subtitles

An independent Linux OpenXR overlay for local spoken subtitles and scene text
translation. It builds, installs, configures, and runs without WiVRn source,
Python modules, IPC extensions, or headset modifications. Its current runtime
requirement is **XR_EXTX_overlay**, supported by the Monado/WiVRn runtime. It
is not a Quest-native standalone application or a universal SteamVR overlay.

All inference runs on an explicitly selected Intel GPU through OpenVINO.
PyTorch XPU is used by training tools. Speech audio, images, recognized text,
and translations never go to an external inference service. There are no API
keys, provider URLs, OpenAI/DeepSeek clients, or ChatGPT/Codex subprocesses.
The installed services restrict sockets to local Unix/netlink families;
Hugging Face and Transformers are also in offline mode. Setup/training tools
explicitly download public checkpoints and datasets.

## Install and start

Build dependencies: CMake, C++17, OpenXR loader/development headers, Vulkan,
SDL2, X11, epoxy, Cairo, Pango, nlohmann-json. GUI: system Python with PySide6.
Inference: Python 3.12, uv, Intel GPU OpenCL drivers, and `parec` (PulseAudio
utilities, using the local PipeWire Pulse server).

```sh
./setup-models.sh
./install.sh
~/.local/opt/spatial-subtitles/bin/spatial-subtitles
```

Select a completed local translation export in the separate GUI before enabling
translation. Missing models produce a local error, never a cloud fallback.
The launcher starts `spatial-subtitles.target` and opens settings. Stop with
`systemctl --user stop spatial-subtitles.target`. Services deliberately do not
depend on or control a WiVRn service. Set `XR_RUNTIME_JSON` in the user service
environment when a non-default OpenXR runtime is needed.

Config: `~/.config/spatial-subtitles/settings.json`. Models and recordings:
`~/.local/share/spatial-subtitles`. Temporary captures and overlay state stay
under `$XDG_RUNTIME_DIR`. Paths respect their XDG equivalents. Settings reload
without restarting VR; in-flight results from old settings are discarded.

Choose a **playback sink**, not a microphone. The default monitors the default
PipeWire output; a WiVRn installation can select `wivrn.sink` as an ordinary
PipeWire sink. Capture uses its Pulse monitor, including virtual sinks without
native monitor ports, and does not reconnect to another source automatically.
Speech and scene OCR have separate enable switches. English,
Serbian (Cyrillic), and Original are output presets. Source language Auto uses
script detection; Japanese containing only kanji can be mistaken for Chinese,
so an explicit source setting is available.

Voice colours use the local anonymous speaker model and remember up to four
voices per session. The default assigns one colour to each short audio phrase,
avoiding timestamp decoding. Enable “Split voices within a speech chunk” for
finer, slower timestamp-based assignments. Stereo direction is approximate; overlapping voices are
not separated. Model directories, size, font, duration, OCR confidence, scan
interval, workload limits, and recording controls are editable in the GUI.
Recordings default off. Retention and storage limits apply; review/correct
captured transcripts before training. No scene images or voice embeddings are
saved to transcript logs.

## Scene text capture

OpenXR overlays cannot read another application's projection images. Launch
native Vulkan OpenXR applications through the independent, **explicit** layer:

```sh
~/.local/opt/spatial-subtitles/bin/spatial-subtitles-capture game-command args...
```

This captures the first projection layer at up to two image copies per second.
A private three-second lease from the backend gates capture: disabling OCR or
stopping the backend stops new image copies.
No global implicit layer is installed. Ordinary games, headset passthrough,
system UI, and this app's own quad overlays are not captured. D3D/Proton and
OpenGL application capture are not implemented. OCR uses the left view's FOV,
a flat approximate plane, matching apparent foreground/background colours,
and a recent overlay tracking pose. Results hide after head motion to limit
misalignment. Moving objects, view timing offsets, oblique signs, and hand-drawn
text remain experimental. Exact depth reconstruction is not attempted.

The capture implementation is derived from OpenXR-Layer-OBSMirror (MIT,
Matthieu Bucchianeri), based on revision
`77de9c0a3a8d9d794f23f1a20b0b76f3ab53a0bd` and local Linux fixes. Its license is
in `native/capture/LICENSE`. This app adds a separate explicit layer name,
transport, FOV metadata, and throttling. It does not install the OBS plugin.
Jupiteroid is CC0; Pango uses installed fallback fonts for other scripts.

## Training and validation

`training/setup.sh` provisions a separate XPU training environment and the
pinned Whisper checkpoint. `training/prepare_translation.py` aligns public Google FLEURS sentences by ID
within their official train/validation/test splits. No validation/test pairs
are used for gradients. The NLLB-200 distilled 600M backbone is pinned to
`f8d333a098d19b4fd9a8b18f94170487ad3f821d` (CC-BY-NC-4.0). It supports Japanese,
Korean, Chinese and Thai to English; Serbian output uses a local English pivot.
The translation pilot trains decoder LoRA weights on XPU. Selection uses
held-out validation; `compress_translation.py` explicitly applies NNCF INT8
weight compression, and inference is checked separately in OpenVINO.

Speech adaptation uses Whisper large-v3-turbo, encoder/decoder LoRA, and 120
training/20 validation clips per language. OCR trains final CTC heads on
synthetic signs from separate FLEURS phrases, including Thai. A small trained
script classifier selects one OCR head for confident crops, with all-head
fallback on uncertainty. This is the OCR speed optimization; head training
alone does not make the network faster. Synthetic validation does not establish
real-world scene or handwriting accuracy.

Models and source media are local artifacts, not bundled in Git or the Quest
APK. FLEURS is CC-BY-4.0. NLLB and the existing Emilia-derived speaker calibration
are for non-commercial use. Source cards, pinned revisions and metrics are
retained with the local model exports and aggregate reports. Never describe a
regressing candidate as an improvement or activate it silently.

The [6 October training report](training/reports/README.md) records the completed
pilots and selection. Trained OCR heads/router are active on this installation.
Speech and translation adaptations regressed parts of validation, so their
local pretrained baselines remain selected. Rejected experiments are retained
for research, not automatically deployed.

Live verification confirmed upright text in the headset and speech/OCR quad
submission. Playback-monitor counters are exposed in `status.json` without
transcript logging. Native OpenVINO GPU faults occurred during earlier live
tests; the service restarts on failure, and Python fault traces are enabled.
Subsequent scene stress/playback tests passed, but long-session GPU stability
is not yet established.

Run Python tests with `python3 -m unittest discover -s tests`, native capture
checks with `ctest --test-dir build --output-on-failure`, and local model
benchmarks from `training/`. GPU timing should be repeated without simultaneous
training or VR load before treating it as a performance estimate.
