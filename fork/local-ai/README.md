# Local AI moved to Spatial Subtitles

Use the independent [Spatial Subtitles application](../../apps/spatial-subtitles/README.md).
`setup.sh` forwards to its standalone setup. `doctor.py` uses its recognition
module. The historical training scripts and reports in `training/` remain for
reproducibility; new four-language work is in
[`apps/spatial-subtitles/training`](../../apps/spatial-subtitles/training).

Inference has no external API, ChatGPT agent, or network fallback. All model
selection and recording options are in the separate Spatial Subtitles GUI.
