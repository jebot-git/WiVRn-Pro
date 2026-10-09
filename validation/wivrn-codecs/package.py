#!/usr/bin/env python3
"""Deterministic packaging for these standalone testing artifacts."""
import gzip
import argparse
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path
from plans import make_plan
from runner import save

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
BUILDS=ROOT/"testing-builds"


def digest(path):
    with path.open("rb") as f: return hashlib.file_digest(f,"sha256").hexdigest()


def seal(folder):
    files={str(p.relative_to(folder)):digest(p) for p in sorted(folder.rglob("*"))
           if p.is_file() and p.name not in ("manifest.json","SHA256SUMS") and "__pycache__" not in p.parts}
    # Include nested bundle manifests/checksums too; only exclude our own pair.
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.parent!=folder and p.name in ("manifest.json","SHA256SUMS"):
            files[str(p.relative_to(folder))]=digest(p)
    save(folder/"manifest.json",dict(schema=1,bundle=folder.name,files=dict(sorted(files.items()))))
    files["manifest.json"]=digest(folder/"manifest.json")
    (folder/"SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k,v in sorted(files.items())))
    # Names contain a semver suffix: do not replace its final '.0'.
    archive=folder.parent/(folder.name+".tar.gz")
    with archive.open("wb") as raw, gzip.GzipFile(filename="",fileobj=raw,mode="wb",mtime=0) as gz, tarfile.open(fileobj=gz,mode="w|") as tar:
        for path in [folder]+sorted(folder.rglob("*")):
            if "__pycache__" in path.parts: continue
            if path.is_symlink(): raise ValueError("symlinks are not allowed in a frozen payload")
            info=tar.gettarinfo(str(path),arcname=str(path.relative_to(folder.parent)))
            info.uid=info.gid=info.mtime=0; info.uname=info.gname=""
            if path.is_file():
                with path.open("rb") as src: tar.addfile(info,src)
            else: tar.addfile(info)
    sha=digest(archive)
    Path(str(archive)+".sha256").write_text(sha+"  "+archive.name+"\n")
    return dict(path=str(archive),sha256=sha,bytes=archive.stat().st_size,files=len(files))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.3.0")
    args=parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",args.version):
        parser.error("version must be MAJOR.MINOR.PATCH")
    vulkan=BUILDS/"vulkan-vr444-0.2.0"
    v=dict(path=str(vulkan.parent/(vulkan.name+".tar.gz")),sha256=digest(vulkan.parent/(vulkan.name+".tar.gz")))
    suite=BUILDS/("wivrn-codec-suite-"+args.version)
    if suite.exists(): raise FileExistsError("choose a fresh suite version; staging already exists")
    harness=HERE/"hardware/build/codec-harness.jar"
    if not harness.is_file(): raise FileNotFoundError("build hardware/build.sh before packaging")
    shutil.copytree(HERE,suite,ignore=shutil.ignore_patterns("__pycache__","*.pyc","build"))
    (suite/"hardware/android").mkdir(parents=True)
    shutil.copy2(harness,suite/"hardware/android/codec-harness.jar")
    shutil.copy2(ROOT/"LICENSE-MIT",suite/"LICENSE-MIT")
    for name in ("hevc-carrier-0.1.0","vulkan-vr444-0.2.0"):
        shutil.copytree(BUILDS/name,suite/"bundles"/name,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    (suite/"plans").mkdir()
    for tier in ("smoke","full","soak","optical"): save(suite/"plans"/(tier+".json"),make_plan(tier))
    save(suite/"BUILD.json",dict(name="wivrn-codec-suite",version=args.version,
                                deployment="standalone ADB hardware runner; actual WiVRn adapter pending",
                                hardware_validation_scope="See HARDWARE.txt and per-run hashed evidence; no WiVRn or optical qualification",
                                codec_bundles=["hevc-carrier-0.1.0","vulkan-vr444-0.2.0"],
                                media_harness=dict(min_api=29,compile_api=36,build_tools="36.1.0",sha256=digest(harness))))
    print(json.dumps(dict(vulkan=v,suite=seal(suite)),indent=2))


if __name__=="__main__": main()
