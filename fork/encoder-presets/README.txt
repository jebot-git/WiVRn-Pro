WiVRn Pro host encoder presets — A770 / Quest Pro — 2026-10-08

Ready-to-use host configuration presets and a selector are in this directory.
This selector changes saved host configuration only when its apply command is
invoked. In the desktop dashboard, choose a preset and Load preset, then Save
WiVRn settings. CQP presets expose an adjustable QP (1..51). Loading only edits
the draft; saved encoder changes take effect on the next streaming session.

Profiles
  hevc-latency-50             HEVC Main CBR; set headset to 50 Mbit/s
  hevc-latency-75             HEVC Main CBR; set headset to 75 Mbit/s
  hevc-balanced-100           HEVC Main CBR; set headset to 100 Mbit/s
  hevc-quality-150            HEVC Main CBR; set headset to 150 Mbit/s
  avc-ab-100                  H.264 Main CBR comparison; headset 100 Mbit/s
  hevc-cqp-24-experimental    HEVC CQP 24; CQP-capable branch required, uncapped
  hevc-cqp-18-experimental    HEVC CQP 18; CQP-capable branch required, uncapped
  avc-cqp-24-experimental     H.264 Main CQP 24; CQP-capable branch required, uncapped

The four HEVC CBR profiles deliberately produce the same host options. Their
different bitrates MUST be selected in the headset UI. The current host parser
does not read the legacy top-level bitrate, scale or encoders keys still present
in the user's config. The preset files keep headset instructions separate from
the JSON sent to the host. They do not pretend to remotely set those controls.

All profiles use QSV on the host-selected Intel Arc, 8-bit 4:2:0, veryfast,
async_depth=1, lookahead disabled, forced_idr=1, low_power=1. HEVC uses gpb=0;
H.264 uses look_ahead=0. CBR uses low_delay_brc=1; CQP uses 0. UDP transport is
allowed (tcp-only=false). forced_idr=1 makes requested I frames IDRs; it does not
request an IDR every frame. The backend already sets max_b_frames=0 and GOP=240.

On the headset, use 90 Hz, Application SpaceWarp off, Foveated encoding 75%,
matching Video codec, 10-bit color off, Low-latency decoding on, and the stated
bitrate. Preserve current render resolution. The baseline is 576x640 encoded
pixels per eye; a different render-resolution setting changes that raster even
at the same foveation percentage. The named headset "Stream quality preset"
choices use different spatial scales; do not choose one after setting 75%.
Disconnect/reconnect to apply foveation, codec and decoder changes. A reconnect
must create a new encoder/session for saved host settings to be picked up.

Usage (from this directory)
  ./wivrn-host-preset list
  ./wivrn-host-preset show hevc-quality-150
  ./wivrn-host-preset render hevc-quality-150 --output /tmp/wivrn-quality-review.json
  ./wivrn-host-preset apply hevc-quality-150

render reads the current user config by default, replaces only encoder,
bit-depth and tcp-only, and writes a separate reviewable file (or JSON to stdout).
Use --base /path/config.json to merge another base. Existing render output files
are not overwritten. The rendered/ directory contains concrete results merged
with the user's config during this task; these snapshots do not track later edits.

apply defaults to $XDG_CONFIG_HOME/wivrn-pro/config.json or
~/.config/wivrn-pro/config.json. It preserves unrelated top-level values and any
legacy ignored keys; it replaces the whole encoder object so options from a
previous codec cannot leak into the new one. It backs up the exact old bytes,
preserves file permissions, then atomically saves. Invalid JSON and symlink
configs are rejected. It does not signal/restart the service or change the headset.
Use --config /path/config.json to target another saved config. To restore, copy
the printed .before-* backup over that config and begin a new streaming session.
If launching the server with an explicit configuration path, target that path.

CQP branch usage
  ./wivrn-host-preset render hevc-cqp-24-experimental --cqp-capable --output /tmp/wivrn-cqp-review.json
  ./wivrn-host-preset apply hevc-cqp-24-experimental --cqp-capable

Use --cqp-capable only when the target server supports the new reserved QSV
options rate_control="cqp" and qp="24". It is a capability acknowledgement,
not runtime detection. The existing server silently ignores unsupported options,
so using a CQP profile with the old binary does NOT enable CQP. For a new branch
session, check its logs for explicit CQP selection and QP. The branch must disable
bitrate/VBV control and handle live bitrate updates appropriately; the preset
alone cannot do that. CQP bitrate is content-dependent and the headset bitrate
slider is not a maximum. The same QP is used for both eyes and alpha here; alpha
quality and aggregate traffic require live validation. Switching back to a CBR
profile restores the normal host CBR path and headset-controlled bitrate.

Interpretation of profiles
HEVC CBR 50/100/150 and CQP 24/18 are anchored to the previous 576x640 synthetic
single-eye tests. 75 Mbit/s CBR is an interpolation. H.264 profiles are A/B tools;
they are not demonstrated to beat HEVC end-to-end on this Quest Pro. CQP24's
approximately 75 Mbit/s HEVC / 77 Mbit/s H.264 and CQP18's approximately 195 Mbit/s
HEVC aggregate figures were projections from one synthetic eye, not bandwidth
caps or real-game measurements. These presets are not guaranteed latency fixes.

Source and verification
Inspected source: /home/blux/kwin/spatial-anchoring/wivrn-pro-keepalive/source
  server/driver/configuration.cpp: parse_encoder and top-level config merge
  server/encoder/encoder_settings.cpp: get_encoder_settings and split_bitrate
  server/encoder/ffmpeg/video_encoder_va.cpp: open_codec
  client/scenes/gui_settings.cpp: Video and Streaming settings
See ../wivrn-operating-points-20261008/report.txt for measured operating points.
Eight automated checks passed (validation.txt): config preservation and backup,
permissions, render isolation, overwrite refusal, malformed-JSON refusal,
symlink refusal, CQP guard, and all FFmpeg private-option names on the installed
HEVC/H.264 QSV encoders. No GPU workload was launched during this preset task.
These checks do not certify a full headset streaming session with each profile.

CQP aggregate-rate caveat: equal QP on both eyes and alpha does not preserve the
CBR 1:1:0.025 rate allocation. Per-eye projections are not a validated total
network bitrate; mask complexity and keyframes can materially change traffic.

Installed selector: ~/.local/opt/wivrn-pro/bin/wivrn-pro-encoder-preset
Example: wivrn-pro-encoder-preset list (use the full path if not on PATH).
The desktop dashboard includes the same eight profiles. Loading a preset
preserves other unsaved controls and unrelated JSON. The dashboard CQP quantizer
can override the starting value in a named CQP profile. Saving uses the existing
D-Bus conflict check so external config edits are not silently overwritten.
