#!/usr/bin/env python3
"""Summarize completed standalone JSON evidence without changing raw files."""
from pathlib import Path
import hashlib
import json
import statistics

ROOT = Path(__file__).resolve().parents[4]
SOURCE = Path(__file__).resolve().parent
RESULT = ROOT / 'validation/results/codec-viability-0.8.0/compact'
PROFILES = {
    'host-scalar-vs-coop128': 'summary-host',
    'quest-scalar-vs-coop128': 'summary-quest',
    'host-unconditional-vs-coop128': 'summary-host-unconditional',
    'quest-unconditional-vs-coop128': 'summary-quest-unconditional',
}
GROUPS = {
    'paired-q8': ('paired-a/', 'paired-b/'),
    'lossless-q1': ('lossless/',),
    'synthetic': ('synthetic/',),
}
for raw_name, summary_name in PROFILES.items():
    path = RESULT / (raw_name + '.json')
    if not path.exists():
        continue
    raw = json.loads(path.read_text())
    assert raw['status'] == 'pass' and len(raw['cases']) == 38
    out = {
        'method': 'Arithmetic mean of per-case medians; equal case weights. '
                  'Five measured repetitions; warmups excluded. '
                  'Full RGBA readback included; not optical latency.',
        'source': path.name,
        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'device': raw['device'],
        'compact_legacy_inverse': raw['compact_legacy_inverse'],
        'groups': {},
    }
    for name, prefixes in GROUPS.items():
        cases = [case for case in raw['cases'] if case['name'].startswith(prefixes)]
        assert cases and all(case['rgba_exact'] and case['all_tile_status_zero']
                             for case in cases)
        data = {'cases': len(cases)}
        for field in ('compact_wire_bytes', 'compact_input_copy_bytes',
                      'legacy_input_copy_bytes'):
            data[field + '_sum'] = sum(case[field] for case in cases)
        data['input_copy_ratio'] = (data['compact_input_copy_bytes_sum'] /
                                    data['legacy_input_copy_bytes_sum'])
        data['constant_tile_fraction'] = (sum(c['constant_tiles'] for c in cases) /
                                         sum(c['tiles'] for c in cases))
        for field in ('compact_prepare_ms', 'compact_gpu_ms', 'legacy_gpu_ms',
                      'compact_total_ms', 'legacy_total_ms'):
            data[field + '_mean_case_p50'] = statistics.mean(
                case[field]['p50'] for case in cases)
        out['groups'][name] = data
    (RESULT / (summary_name + '.json')).write_text(json.dumps(out, indent=2) + '\n')

# Source and binary hashes identify the reviewed source snapshot and measured
# binaries independently. Earlier binaries are preserved rather than overwritten.
files = [p for p in SOURCE.iterdir() if p.is_file()]
for build in ('build', 'build-unconditional', 'build-reviewed',
              'build-unconditional-reviewed'):
    directory = RESULT / build
    files += [p for p in directory.rglob('*')
              if p.is_file() and (p.suffix == '.spv' or p.name.startswith('compact_gpu_probe'))]
manifest = {str(path.relative_to(ROOT)): {
    'bytes': path.stat().st_size,
    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
} for path in sorted(files)}
(RESULT / 'source-build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
