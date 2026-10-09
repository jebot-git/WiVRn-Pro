#!/usr/bin/env python3
"""Read-only Quest test capability inventory. Never roots or changes settings."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

PROBES = {
    "identity": "id; getenforce; getprop ro.build.fingerprint; getprop ro.build.version.incremental; getprop ro.build.type",
    "root_availability": "command -v su || true; ls -l /system/bin/su /system/xbin/su /sbin/su /debug_ramdisk/su 2>&1",
    "singularity_version": "dumpsys package com.lumi.singularity | grep -E 'versionName=|versionCode='",
    "gpu": "for f in /sys/class/kgsl/kgsl-3d0/gpu_model /sys/class/kgsl/kgsl-3d0/gpuclk /sys/class/kgsl/kgsl-3d0/max_gpuclk /sys/class/kgsl/kgsl-3d0/gpubusy /sys/class/kgsl/kgsl-3d0/devfreq/available_frequencies /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq /sys/class/kgsl/kgsl-3d0/devfreq/governor /sys/class/kgsl/kgsl-3d0/devfreq/min_freq /sys/class/kgsl/kgsl-3d0/devfreq/max_freq; do echo $f; cat $f 2>&1; done",
    "cpu": "for f in /sys/devices/system/cpu/cpufreq/policy*/scaling_cur_freq /sys/devices/system/cpu/cpufreq/policy*/scaling_governor /sys/devices/system/cpu/cpufreq/policy*/scaling_available_frequencies; do echo $f; cat $f 2>&1; done",
    "thermal": "dumpsys thermalservice; for d in /sys/class/thermal/thermal_zone*; do echo $d; cat $d/type $d/temp 2>&1; done",
    "battery": "dumpsys battery; for f in /sys/class/power_supply/battery/current_now /sys/class/power_supply/battery/voltage_now /sys/class/power_supply/battery/temp; do echo $f; cat $f 2>&1; done",
    "trace_access": "ls -ld /sys/kernel/tracing /sys/kernel/debug/tracing; cat /sys/kernel/tracing/available_events 2>&1; command -v perfetto; command -v simpleperf; command -v atrace; atrace --list_categories",
    "display": "dumpsys SurfaceFlinger --display-id; getprop debug.oculus.refreshRate; getprop debug.oculus.cpuLevel; getprop debug.oculus.gpuLevel; getprop debug.oculus.foveation.level; getprop debug.oculus.foveation.dynamic",
    "codec_vendor_options": "for f in /vendor/etc/media_codecs*.xml; do echo $f; grep -i -E 'hevc|low.latency|low_latency|latency|performance-point' $f; done",
    "network_tools": "command -v ip; command -v tc; command -v tcpdump; ip -s link show wlan0; cat /proc/sys/net/core/rmem_max /proc/sys/net/core/wmem_max 2>&1",
    "perf_permissions": "cat /proc/sys/kernel/perf_event_paranoid /proc/sys/kernel/kptr_restrict 2>&1; ls -l /dev/kgsl-3d0 /dev/dri/* 2>&1",
}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--serial',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False);result={"schema":1,"serial":a.serial,"scope":"non-root read-only; root capabilities are not assumed","probes":{}}
    for name,script in PROBES.items():
        try:
            proc=subprocess.run(['adb','-s',a.serial,'shell',script],capture_output=True,text=True,timeout=30)
            text=proc.stdout+proc.stderr;code=proc.returncode
        except subprocess.TimeoutExpired:
            text="probe timed out\n";code=124
        path=a.out/(name+'.txt');path.write_text(text)
        result['probes'][name]={'exit_code':code,'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (a.out/'inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    print(a.out)

if __name__=='__main__':main()
