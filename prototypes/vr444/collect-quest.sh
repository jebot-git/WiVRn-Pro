#!/usr/bin/env bash
# Read-only device inventory plus an explicitly requested standalone probe run.
# Usage: bash collect-quest.sh OUTPUT_DIRECTORY [ADB_SERIAL]
set -euo pipefail
output=${1:?Specify an output directory}
adb_args=()
if [[ -n ${2:-} ]]; then adb_args=(-s "$2"); fi
mkdir -p "$output"
for key in ro.product.model ro.board.platform ro.soc.model ro.build.fingerprint ro.build.version.sdk; do
  printf '%s=' "$key" >> "$output/android-properties.txt"
  adb "${adb_args[@]}" shell getprop "$key" >> "$output/android-properties.txt"
done
adb "${adb_args[@]}" shell cat /proc/meminfo > "$output/android-meminfo.txt"
adb "${adb_args[@]}" shell 'for f in /sys/class/kgsl/kgsl-3d0/gpu_model /sys/class/kgsl/kgsl-3d0/max_gpuclk /sys/class/kgsl/kgsl-3d0/gpuclk /sys/class/kgsl/kgsl-3d0/devfreq/available_frequencies; do echo "$f"; cat "$f" 2>&1 || true; done' > "$output/adreno-observations.txt"
adb "${adb_args[@]}" shell /data/local/tmp/vr444/vr444-probe > "$output/vulkan-capabilities.json"
python3 -m json.tool "$output/vulkan-capabilities.json" > /dev/null
printf 'Inventory saved to %s. Clock readings are observations, not sustained performance guarantees.\n' "$output"
