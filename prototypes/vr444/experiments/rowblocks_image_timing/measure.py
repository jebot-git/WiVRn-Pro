#!/usr/bin/env python3
"""Run one coordinated host-only comparison, binding both immutable corpora."""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from audit import audit, matched_variants, source_snapshot


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')


def source_files():
    here=Path(__file__).resolve().parent;root=here.parents[3]
    common=root/'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444'
    native=here.parent/'native_lz4'
    pending=[here/'probe.cpp',common/'codec.cpp',native/'vendor/lz4.c',
             here.parent/'rowfilter_gpu/decode.comp',here.parent/'rowblocks_gpu/decode.comp',
             here.parent/'direct_image_gpu/sample.comp']
    found=set()
    while pending:
        path=pending.pop().resolve()
        if path in found:continue
        found.add(path)
        for name in re.findall(r'^\s*#\s*include\s*"([^"]+)"',path.read_text(),re.M):
            choices=[path.parent/name,common/name,native/name]
            target=next((p for p in choices if p.is_file()),None)
            if target is None:raise ValueError(f'unresolved local include {path}: {name}')
            pending.append(target)
    return {str(p.relative_to(root)):digest(p) for p in sorted(found)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','shaders','rrf_corpus','rrb_corpus','rrf_manifest','rrb_manifest','output'):p.add_argument(name,type=Path)
    p.add_argument('--research-teams-idle',action='store_true',required=True)
    p.add_argument('--icd',type=Path,required=True)
    p.add_argument('--device',type=int,choices=(0,1),default=0)
    args=p.parse_args();paths=[args.rrf_manifest,args.rrb_manifest];raw=[p.read_bytes() for p in paths];manifests=[json.loads(b) for b in raw]
    matched_variants(*manifests)
    snapshot=lambda:[source_snapshot(args.rrf_corpus,manifests[0]),source_snapshot(args.rrb_corpus,manifests[1],True)]
    before=snapshot();sources=source_files();shader_snapshot=lambda:{str(p):digest(p) for p in sorted(args.shaders.rglob('*.spv'))}
    shaders=shader_snapshot()
    if len(shaders)!=8:raise ValueError('complete two-family shader matrix required')
    args.output.mkdir(parents=True,exist_ok=False);write(args.output/'input-hashes.json',before)
    conditions=dict(start_utc=utc(),research_teams_confirmed_idle=True,desktop_workload='uncontrolled',headset_used=False,
                    manifest_sha256=[hashlib.sha256(b).hexdigest() for b in raw],binary_sha256=digest(args.binary),
                    source_sha256=sources,shader_sha256=shaders,icd=str(args.icd),repetitions=31,selected_device_index=args.device)
    command=[str(args.binary),str(args.shaders),str(args.rrf_corpus),str(args.rrb_corpus),'--reps','31','--device',str(args.device)]
    env=dict(os.environ,VK_ICD_FILENAMES=str(args.icd));start=time.perf_counter()
    with (args.output/'result.json').open('x') as out,(args.output/'stderr.txt').open('x') as err:
        result=subprocess.run(command,stdout=out,stderr=err,env=env,check=False)
    conditions.update(finish_utc=utc(),process_seconds=time.perf_counter()-start,exit_code=result.returncode,
        manifests_unchanged=[p.read_bytes() for p in paths]==raw,corpora_unchanged=snapshot()==before,
        binary_unchanged=digest(args.binary)==conditions['binary_sha256'],sources_unchanged=source_files()==sources,
        shaders_unchanged=shader_snapshot()==shaders)
    write(args.output/'conditions.json',conditions)
    if result.returncode or not all(conditions[k] for k in ('manifests_unchanged','corpora_unchanged','binary_unchanged','sources_unchanged','shaders_unchanged')):
        raise RuntimeError('failed measurement or changed input; retain evidence')
    report=audit(json.loads((args.output/'result.json').read_text()),*manifests);report['pass']=report.pop('pass_')
    report.update(result_sha256=digest(args.output/'result.json'),conditions=conditions);write(args.output/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('per_case','conditions')},indent=2))


if __name__=='__main__':main()
