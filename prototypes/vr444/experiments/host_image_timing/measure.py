#!/usr/bin/env python3
"""Record one coordinated host batch; never launches a headset or network task."""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from audit import audit, source_snapshot


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('shaders', type=Path)
    parser.add_argument('corpus', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--research-teams-idle', action='store_true', required=True,
                        help='Record prior explicit idle coordination; this does not stop other processes.')
    parser.add_argument('--icd', type=Path, required=True)
    parser.add_argument('--device', type=int, choices=(0, 1), required=True)
    args = parser.parse_args()
    raw_manifest = args.manifest.read_bytes()
    manifest = json.loads(raw_manifest)
    before = source_snapshot(args.corpus, manifest)
    shader_snapshot = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(args.shaders.glob('shaders-*/*.spv'))}
    shaders_before = shader_snapshot()
    if len(shaders_before) != 12:
        raise ValueError('complete qualified shader matrix required')
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'input-hashes.json', before)
    conditions = dict(start_utc=utc(), research_teams_confirmed_idle=args.research_teams_idle,
                      desktop_workload='uncontrolled', headset_used=False,
                      corpus_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
                      binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
                      icd=str(args.icd), repetitions=31, selected_device_index=args.device,
                      shader_sha256=shaders_before)
    env = dict(os.environ, VK_ICD_FILENAMES=str(args.icd))
    command = [str(args.binary), str(args.shaders), str(args.corpus), '--reps', '31', '--device', str(args.device)]
    start = time.perf_counter()
    with (args.output/'result.json').open('x') as output, (args.output/'stderr.txt').open('x') as error:
        result = subprocess.run(command, stdout=output, stderr=error, env=env, check=False)
    conditions.update(finish_utc=utc(), process_seconds=time.perf_counter()-start, exit_code=result.returncode)
    conditions['manifest_unchanged'] = args.manifest.read_bytes() == raw_manifest
    conditions['corpus_unchanged'] = source_snapshot(args.corpus, manifest) == before
    conditions['binary_unchanged'] = hashlib.sha256(args.binary.read_bytes()).hexdigest() == conditions['binary_sha256']
    conditions['shaders_unchanged'] = shader_snapshot() == shaders_before
    write(args.output/'conditions.json', conditions)
    if result.returncode or not all(conditions[k] for k in ('corpus_unchanged', 'manifest_unchanged', 'binary_unchanged', 'shaders_unchanged')):
        raise RuntimeError('measurement failed or source corpus changed; retain raw evidence')
    raw = (args.output/'result.json').read_bytes()
    report = audit(json.loads(raw), manifest)
    report['pass'] = report.pop('pass_')
    report['result_sha256'] = hashlib.sha256(raw).hexdigest()
    report['conditions'] = conditions
    write(args.output/'report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'per_case'}, indent=2))


if __name__ == '__main__': main()
