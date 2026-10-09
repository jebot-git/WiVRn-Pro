#!/usr/bin/env python3
"""Entry point for standalone tests, live WiVRn correctness tests, and qualification adapters."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from plans import make_plan
from runner import run, save, readiness
from trace import analyze
from optical import analyze_optical

ROOT = Path(__file__).resolve().parent


def smoke(bundles, output):
    output.mkdir(parents=True, exist_ok=False)
    commands = [([sys.executable,"-m","unittest","discover","-s",str(ROOT/"tests"),"-v"],ROOT,"suite-unit"),
                ([sys.executable,"-m","unittest","test_atlas","-v"],bundles/"hevc-carrier-0.1.0","hevc-carrier"),
                ([sys.executable,"-m","unittest","test_transport","-v"],bundles/"vulkan-vr444-0.2.0/src","transport")]
    binary = bundles/"vulkan-vr444-0.2.0/linux-x86_64/vr444"
    shaders = binary.parent
    commands.extend([([str(binary),flag],ROOT,flag[2:]) for flag in ("--self-test","--foveation-test")])
    commands.append(([str(binary),"--gpu-test",str(shaders)],ROOT,"vulkan-default"))
    for kernel in ("coop64","coop128"):
        commands.append((["env","VR444_KERNEL="+kernel,str(binary),"--gpu-test",str(shaders)],ROOT,"vulkan-"+kernel))
    reports = []
    for argv,cwd,name in commands:
        with open(output/(name+".log"),"w") as log:
            try:
                result = subprocess.run(argv,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,timeout=180)
                code = result.returncode
            except (OSError,subprocess.TimeoutExpired) as error:
                log.write(str(error)); code = -1
        reports.append(dict(test=name,argv=argv,exit_code=code))
    result = dict(status="pass" if all(r["exit_code"]==0 for r in reports) else "fail", scope="standalone only; this cannot certify A770/Quest or WiVRn integration", tests=reports)
    save(output/"summary.json",result)
    return result


def verify(root):
    manifest = json.loads((root/"manifest.json").read_text())
    errors = []
    for filename,expected in manifest["files"].items():
        path = (root/filename).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append(filename+": missing/invalid"); continue
        with path.open("rb") as source:
            actual = hashlib.file_digest(source,"sha256").hexdigest()
        if actual != expected:
            errors.append(filename+": checksum")
    return dict(status="pass" if not errors else "fail", checked_files=len(manifest["files"]), errors=errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    p = sub.add_parser("plan"); p.add_argument("--tier",choices=("smoke","full","soak","optical"),default="full"); p.add_argument("--seed",type=int,default=444); p.add_argument("--out",required=True)
    p = sub.add_parser("analyze-optical"); p.add_argument("capture"); p.add_argument("--calibration",required=True); p.add_argument("--config",required=True); p.add_argument("--out",required=True)
    p = sub.add_parser("analyze"); p.add_argument("trace"); p.add_argument("--out",required=True)
    p = sub.add_parser("local-smoke"); p.add_argument("--bundles",type=Path,required=True); p.add_argument("--out",type=Path,required=True)
    p = sub.add_parser("readiness"); p.add_argument("probe"); p.add_argument("--plan",required=True)
    p = sub.add_parser("run"); p.add_argument("--adapter",required=True); p.add_argument("--plan",required=True); p.add_argument("--out",required=True); p.add_argument("--case",action="append")
    p = sub.add_parser("verify"); p.add_argument("bundle",type=Path)
    p = sub.add_parser("live-integration"); p.add_argument("--source",type=Path,required=True); p.add_argument("--serial",required=True); p.add_argument("--out",type=Path,required=True); p.add_argument("--mode",choices=("raw-rgba","hevc-carrier","vulkan-vr444"),required=True); p.add_argument("--seconds",type=int,default=35); p.add_argument("--port",type=int,default=9767); p.add_argument("--q",type=int,default=8)
    p.add_argument('--frame-stride',type=int,default=12); p.add_argument('--kernel',choices=('baseline','coop64','coop128'),default='baseline')
    p.add_argument('--decoder-kernel',choices=('baseline','coop64','coop128','coop128dc'))
    p.add_argument('--decoder-backend',choices=('resident','readback'),default='resident')
    p.add_argument('--pairing',choices=('paired','independent'),default='paired')
    p.add_argument('--meta-sr',choices=('off','normal','quality'),default='off')
    p.add_argument('--compression',choices=('none','lz4'),default='none')
    p.add_argument('--gaze-timing',action='store_true')
    p.add_argument('--scene-space',choices=('Local','View'),default='Local')
    p.add_argument('--unattended',action='store_true')
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = make_plan(args.tier,args.seed); save(args.out,result)
            print(json.dumps({k:result[k] for k in ("tier","case_count","run_count","estimated_acquisition_seconds")})); return 0
        if args.command == "analyze-optical":
            result = analyze_optical(json.loads(Path(args.capture).read_text()),json.loads(Path(args.calibration).read_text()),json.loads(Path(args.config).read_text())); save(args.out,result)
        elif args.command == "analyze":
            result = analyze(args.trace); save(args.out,result)
        elif args.command == "readiness":
            blockers = readiness(json.loads(Path(args.probe).read_text()),json.loads(Path(args.plan).read_text())["cases"])
            result = dict(status="blocked" if blockers else "pass",blockers=blockers)
        elif args.command == "run":
            result = run(args.adapter,json.loads(Path(args.plan).read_text()),args.out,args.case)
        elif args.command == "live-integration":
            from integration.live import run as live_run
            if not 10 <= args.seconds <= 120 or not 1024 <= args.port <= 65535 or not 1 <= args.q <= 1024:
                raise ValueError("invalid bounded live test parameters")
            result = live_run(args)
        elif args.command == "verify":
            result = verify(args.bundle)
        else:
            result = smoke(args.bundles.resolve(),args.out.resolve())
        print(json.dumps(result,indent=2,allow_nan=False))
        return 0 if result["status"] in ("pass","software_only") else 2
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as error:
        print(str(error),file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main())
