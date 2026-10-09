#!/usr/bin/env python3
"""Read-only capability evidence collection; no service/config/package changes."""
import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def command(argv):
    if not shutil.which(argv[0]):
        return dict(argv=argv,status="unavailable")
    try:
        p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
        return dict(argv=argv,exit_code=p.returncode,output=p.stdout[:1000000],truncated=len(p.stdout)>1000000)
    except (subprocess.TimeoutExpired,OSError) as e:
        return dict(argv=argv,status="error",error=str(e))


def host(render_node=None, probe=None):
    report=dict(schema=1,platform=platform.platform(),python=platform.python_version(),
                kernel=platform.release(),provenance="unverified",verified=False)
    cmds=[["ffmpeg","-hide_banner","-version"],["ffmpeg","-hide_banner","-h","encoder=hevc_qsv"],
          ["ffmpeg","-hide_banner","-h","encoder=vp9_qsv"],["vpl-inspect"],["vulkaninfo","--summary"]]
    if probe: cmds.append([str(Path(probe).resolve())])
    if render_node:
        name=Path(render_node).name
        root=Path("/sys/class/drm")/name/"device"
        report["render_node"]=render_node
        report["pci"]={k:(root/k).read_text().strip() for k in ("vendor","device","subsystem_vendor","subsystem_device") if (root/k).is_file()}
        report["drm_access"]=os.access(render_node,os.R_OK|os.W_OK)
        cmds.append(["vainfo","--display","drm","--device",render_node])
    report["commands"]=[command(c) for c in cmds]
    report["note"]="Evidence collection does not establish WiVRn integration or target validation. Retain exact iHD/oneVPL/loader versions from deployment package manager."
    return report


def client(serial):
    prefix=["adb","-s",serial,"shell"]
    commands=[prefix+["getprop",p] for p in ("ro.product.model","ro.board.platform","ro.build.fingerprint","ro.build.version.sdk")]
    commands += [prefix+["cat","/proc/meminfo"],prefix+["dumpsys","thermalservice"],prefix+["dumpsys","battery"]]
    return dict(schema=1,provenance="unverified",verified=False,commands=[command(c) for c in commands],
                required_in_app_probes=["MediaCodecInfo profile/level/size/rate capabilities and actual configure/decode", "OpenXR extension/gaze permission/session validity", "Vulkan probe and real AHardwareBuffer imports", "display refresh/colour/range/stride"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("side",choices=("host","client")); p.add_argument("--serial"); p.add_argument("--render-node"); p.add_argument("--vulkan-probe"); p.add_argument("--out",required=True)
    a=p.parse_args()
    if a.side=="client" and not a.serial: p.error("client requires an explicit adb serial")
    result=host(a.render_node,a.vulkan_probe) if a.side=="host" else client(a.serial)
    Path(a.out).write_text(json.dumps(result,indent=2)+"\n")
