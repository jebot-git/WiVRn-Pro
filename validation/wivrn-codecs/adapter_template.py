#!/usr/bin/env python3
"""Safe adapter scaffold. Probe truthfully reports absent WiVRn integration.

Implement actual target-controlled probe/run/stop operations in a new adapter;
do not change capability flags until the corresponding integration exists.
"""
import argparse
import json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("probe","run","stop"));p.add_argument("--request",required=True);p.add_argument("--response",required=True)
    a=p.parse_args(); request=json.loads(Path(a.request).read_text())
    if request.get("schema")!=1: raise ValueError("unsupported request schema")
    if a.action=="probe":
        result=dict(schema=1,provenance="standalone-unintegrated",host=dict(verified=False),client=dict(verified=False),
                    implementations={name:dict(wivrn_integrated=False) for name in ("hevc-carrier","vulkan-vr444")},
                    reason="Frozen standalone bundles exist; WiVRn host and Android client adapters are pending.")
    elif a.action=="stop":
        result=dict(schema=1,restored=True,note="Scaffold performs no mutations or launches.")
    else:
        result=dict(schema=1,status="blocked",run_id=request.get("run_id"),reason="WiVRn integration is not implemented")
    Path(a.response).write_text(json.dumps(result,indent=2)+"\n")
    return 2 if a.action=="run" else 0

if __name__=="__main__": raise SystemExit(main())
