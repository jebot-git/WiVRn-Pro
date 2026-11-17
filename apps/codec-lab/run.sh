#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mode=${1:---xr}
if [[ $mode == --desktop || $mode == --xr ]]; then shift || true; else mode=--xr; fi
args=(--path "$PWD" --rendering-method mobile --audio-driver Dummy)
if [[ $mode == --desktop ]]; then
    args+=(--xr-mode off)
else
    args+=(--xr-mode on)
    if [[ -z ${XR_RUNTIME_JSON:-} ]]; then
        for manifest in /usr/local/share/openxr/1/openxr_wivrn_pro.json /usr/share/openxr/1/openxr_wivrn.json; do
            if [[ -f $manifest ]]; then export XR_RUNTIME_JSON=$manifest; break; fi
        done
    fi
fi
# Wayland is the default; X11 remains an explicit user override.
args+=(--display-driver "${CODEC_LAB_DISPLAY_DRIVER:-wayland}")
exec "${GODOT:-godot}" "${args[@]}" -- "$mode" "$@"
