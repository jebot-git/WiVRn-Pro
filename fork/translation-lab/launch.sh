#!/usr/bin/env bash
set -euo pipefail
lab_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export XR_RUNTIME_JSON="${XR_RUNTIME_JSON:-$HOME/.local/opt/wivrn-pro/share/openxr/1/openxr_wivrn_pro.json}"
export PULSE_SINK=wivrn.sink
capture="$HOME/.local/opt/spatial-subtitles/bin/spatial-subtitles-capture"
if [[ -x "$capture" ]]; then
    exec "$capture" godot --path "$lab_dir" --audio-driver PulseAudio "$@"
fi
exec godot --path "$lab_dir" --audio-driver PulseAudio "$@"
