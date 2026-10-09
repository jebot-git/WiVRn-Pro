#!/usr/bin/env python3
"""Reanalyze a frozen diagnostic run without modifying its original result."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]
INTEGRATION = ROOT / 'validation/wivrn-codecs/integration'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def analyze(run):
    run = Path(run).resolve()
    manifest_path = run / 'hashes.json'
    manifest_hash = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if type(manifest) is not dict or not manifest:
        raise ValueError('missing or malformed original evidence manifest')
    required = ('summary.json', 'provenance.json', 'cleanup.json',
                'client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl')
    if not set(required).issubset(manifest):
        raise ValueError('original manifest does not bind all required evidence')
    for name, expected in manifest.items():
        path = run / name
        if (type(name) is not str or Path(name).is_absolute() or '..' in Path(name).parts
                or not path.resolve().is_relative_to(run) or not path.is_file()
                or type(expected) is not str or not re.fullmatch('[0-9a-f]{64}', expected)
                or sha(path) != expected):
            raise ValueError('original manifest mismatch or unsafe path: ' + str(name))
    provenance = json.loads((run / 'provenance.json').read_text())
    original = json.loads((run / 'summary.json').read_text())
    cleanup = json.loads((run / 'cleanup.json').read_text())
    if provenance.get('candidate_trace') is not True:
        raise ValueError('run did not request candidate diagnostics')
    if cleanup.get('restored') is not True or cleanup.get('cleanup_errors'):
        raise ValueError('original run cleanup did not pass')
    path = INTEGRATION / 'candidate_trace.py'
    spec = importlib.util.spec_from_file_location('trace_reanalysis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    traces = [json.loads(line) for line in (run / required[3]).read_text().splitlines()]
    presents = [json.loads(line) for line in (run / required[4]).read_text().splitlines()]
    result = module.evaluate(traces, presents, selection_policy=provenance.get('selection_policy'))
    # Check the inputs again to detect concurrent edits while reanalysis ran.
    for name in required:
        if sha(run / name) != manifest[name]:
            raise ValueError('original evidence changed during reanalysis: ' + name)
    if sha(manifest_path) != manifest_hash:
        raise ValueError('original manifest changed during reanalysis')
    return dict(schema=1, status=result['status'], diagnostic_only=True,
                original_run=str(run), original_run_status=original.get('status'),
                original_candidate_trace=original.get('candidate_trace', {}),
                original_hashes_manifest_sha256=manifest_hash,
                original_summary_sha256=manifest['summary.json'],
                original_manifest_files_verified=len(manifest),
                analyzer_sha256={str(p.relative_to(ROOT)): sha(p) for p in
                                 (Path(__file__), path, INTEGRATION / 'selection_replay.py')},
                reanalysis=result, original_result_overwritten=False,
                qualification_changed=False, promotion_eligible=False, release_ready=False,
                scope='Historical diagnostic interpretation only. No new headset acquisition, '
                      'binary validation, latency promotion, skipped-render outcome proof or optical claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.is_relative_to(run):
        parser.error('reanalysis output must be outside the immutable original run')
    result = analyze(run)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('status', 'original_run_status', 'qualification_changed')}))
    return 0 if result['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
