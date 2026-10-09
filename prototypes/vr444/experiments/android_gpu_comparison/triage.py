#!/usr/bin/env python3
"""Prepare or audit fixed two-repeat Android GPU triage; never invokes ADB."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SMOKE_PIN = '146bab8d10a9e34ca3a78b6fc6634639287a3888'
GPU_PIN = 'c4dc09128e45fc5bda71fa0588e40d2077678b9b'
PREFIX = 'prototypes/vr444/experiments/'
PROFILES = ('corpus/lossless/360', 'corpus/paired-a/1350')
LABELS = ('gpu-repeat-1', 'gpu-repeat-2')
FIELDS = ('joint_outer_wall_ms', 'stereo_total_ms', 'stereo_poison_ms',
          'stereo_preflight_decode_ms', 'direct_preflight_ms', 'direct_decode_ms',
          'stereo_upload_ms', 'stereo_pipeline_wall_ms', 'stereo_device_span_ms',
          'first_consumer_wall_ms', 'first_consumer_device_ms')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


smoke = module('quest_smoke_audit', HERE.parent / 'headset_review_smoke/audit.py')
consumer = module('quest_consumer_audit', HERE.parent / 'mapped_input_consumer/audit.py')
need, same, digest, parse = smoke.need, smoke.same, smoke.digest, smoke.parse


def original(repo):
    raw = smoke.blob(repo, SMOKE_PIN, PREFIX + 'headset_review_smoke/results/plan.json')
    need(digest(raw) == smoke.PLAN_SHA, 'original smoke plan pin')
    return parse(raw)


def plan_for(repo):
    plan = original(repo)
    plan['files'] = [f for f in plan['files'] if f['remote'] not in ('simd-test', 'band-views-test')]
    plan['scope'] = ('Exploratory matched GPU component comparison on rooted Quest Pro; two captured '
                     'profiles and two separate process repeats, seven measured repetitions per arm. '
                     'No OpenXR, p95 qualification or sub15 claim.')
    plan['runs'] = [r for r in plan['runs'] if r['name'] in ('extensions', 'memory')]
    for label in LABELS:
        plan['runs'].append(dict(name=label, timeout_s=180, argv=[
            '{remote}/mapped-consumer', '{remote}/shaders', '{remote}/rrf', '{remote}/rrb',
            '--device', '0', '--reps', '7']))
    return plan


def manifests(repo):
    result, bindings = [], {}
    for family, suffix in (('rgb_rowfilter', 'rrf'), ('rgb_rowblocks', 'rrb')):
        path = PREFIX + family + '/results/corpus-hashes.json'
        raw = smoke.blob(repo, GPU_PIN, path)
        manifest = parse(raw)
        manifest['cases'] = [r for r in manifest['cases']
                             if any(r[suffix].startswith(p.removeprefix('corpus/') + '-') for p in PROFILES)]
        need(len(manifest['cases']) == (4 if suffix == 'rrf' else 16), 'fixed paired manifest population')
        result.append(manifest)
        bindings[path] = digest(raw)
    return result, bindings


def span(values):
    values = sorted(values)
    need(len(values) == 7, 'seven actual observations required')
    return dict(samples=7, median=values[3], minimum=values[0], maximum=values[-1])


def analyze(data, rrf, rrb):
    same(data.get('repetitions'), 7, 'triage repetition count')
    same([c['name'] for c in data.get('cases', [])], list(PROFILES), 'ordered fixed captured population')
    same(data.get('cpu_backend'), 'aarch64-neon', 'actual Android backend')
    checked = consumer.audit(data, rrf, rrb)
    same(checked['right_eye_omission_controls'], 18, 'stereo omission controls')
    result = []
    for case in data['cases']:
        arms = {arm: [r for r in case['iterations'] if r['mode'] == arm] for arm in consumer.ARMS}
        comparisons = []
        for candidate, baseline in (
                ('rrf_direct_default', 'rrf_staged_default'),
                ('rrf_direct_cached', 'rrf_staged_cached'),
                ('rrb32_direct_default', 'rrb32_staged_default'),
                ('rrb32_direct_cached', 'rrb32_staged_cached'),
                ('rrb32_staged_default', 'rrf_staged_default'),
                ('rrb32_staged_cached', 'rrf_staged_cached'),
                ('rrb32_staged_default', 'rrf_cpu_reference'),
                ('rrb32_staged_cached', 'rrf_cpu_reference')):
            fields = {}
            for field in ('joint_outer_wall_ms', 'stereo_preflight_decode_ms', 'stereo_device_span_ms'):
                c, b = [r[field] for r in arms[candidate]], [r[field] for r in arms[baseline]]
                need(all(x > 0 for x in b), 'positive ratio denominator')
                fields[field] = dict(median_difference_ms=span(c)['median']-span(b)['median'],
                                     ratio_of_medians=span(c)['median']/span(b)['median'],
                                     same_rep_ratios=span([x/y for x, y in zip(c, b, strict=True)]))
            comparisons.append(dict(candidate=candidate, baseline=baseline, fields=fields))
        result.append(dict(name=case['name'], device=case['device'], driver=case['driver'],
                           arm_components={a: {f: span([r[f] for r in rows]) for f in FIELDS}
                                           for a, rows in arms.items()},
                           input_memory={a: dict(types=rows[0]['input_memory_type'],
                                                 flags=rows[0]['input_memory_flags']) for a, rows in arms.items()},
                           comparisons=comparisons))
    return dict(recorded_invocations=126, valid_invocations_including_warmups=180,
                omission_controls=18, all_consumer_and_readiness_gates_passed=True, profiles=result)


def audit(plan_path, run_dir, repo, input_root=None):
    helper_bindings = {}
    for pin, rel in ((SMOKE_PIN, 'headset_review_smoke/audit.py'),
                     (GPU_PIN, 'mapped_input_consumer/audit.py'),
                     (GPU_PIN, 'mapped_input_decode/audit.py'),
                     (GPU_PIN, 'rowfilter_image_timing/audit.py')):
        need(digest((HERE.parent/rel).read_bytes()) == digest(smoke.blob(repo, pin, PREFIX+rel)),
             'audit helper differs from pinned implementation')
        helper_bindings[PREFIX+rel] = dict(commit=pin, sha256=digest((HERE.parent/rel).read_bytes()))
    raw_plan, raw_receipt = plan_path.read_bytes(), (run_dir/'receipt.json').read_bytes()
    plan, receipt = parse(raw_plan), parse(raw_receipt)
    same(plan, plan_for(repo), 'exact fixed triage plan')
    same(receipt['plan_sha256'], digest(raw_plan), 'plan binding')
    same(receipt['runner_sha256'], smoke.RUNNER_SHA, 'unchanged bounded runner')
    need(digest((HERE.parent/'headset_review_smoke/run.py').read_bytes()) == smoke.RUNNER_SHA, 'local runner changed')
    for key, value in dict(schema=1, status='completed', device_serial=smoke.SERIAL,
                           headset_used=True, inputs_unchanged=True, root_required=False,
                           openxr_used=False, performance_qualification=False, promotion_eligible=False).items():
        same(receipt.get(key), value, 'execution receipt '+key)
    need(datetime.fromisoformat(receipt['finish_utc']) > datetime.fromisoformat(receipt['start_utc']), 'UTC order')
    same(receipt['inputs'], plan['files'], 'complete inputs')
    remote = receipt['remote_directory']
    need(re.fullmatch('/data/local/tmp/sub15-review-[0-9a-f]{32}', remote), 'owned remote directory')
    expected = smoke.expected_commands(plan, remote)
    need(len(receipt['commands']) == len(expected), 'complete commands')
    outputs, logs, elapsed = {}, {}, {}
    for entry, (label, argv, timeout) in zip(receipt['commands'], expected, strict=True):
        same(entry['label'], label, 'command label'); same(entry['argv'], argv, 'exact argv')
        same(entry['timeout_s'], timeout, 'timeout'); same(entry.get('returncode'), 0, 'nonzero exit')
        same(entry.get('host_timeout', False), False, 'host timeout')
        for suffix in ('stdout', 'stderr'):
            name = label+'.'+suffix
            raw = (run_dir/name).read_bytes()
            same(digest(raw), entry[suffix+'_sha256'], 'raw log binding')
            logs[name] = digest(raw)
            if suffix == 'stdout': outputs[label] = raw
            elif not label.startswith('push-'): need(not raw, 'unexpected stderr')
        elapsed[label] = entry['elapsed_s']
        need(type(elapsed[label]) in (int, float) and math.isfinite(elapsed[label]) and elapsed[label] >= 0,
             'finite process duration')
    same(outputs['serial'].decode().strip(), smoke.SERIAL, 'queried headset identity')
    need(outputs['fingerprint'].decode().strip(), 'queried firmware identity')
    files = {f['remote']: f for f in plan['files']}
    need(len(files) == len(plan['files']) == 39, 'fixed input population')
    for i, item in enumerate(plan['files']):
        path = input_root/item['remote'] if input_root else Path(item['local'])
        same(digest(path.read_bytes()), item['sha256'], 'retained local input')
        same(outputs['hash-'+str(i)].decode().strip().split(),
             [item['sha256'], remote+'/'+item['remote']], 'uploaded input hash')
    provenance = []
    for name, pin, rel, keys in smoke.BINDINGS[:3]:
        body = smoke.blob(repo, pin, PREFIX+rel); historical = parse(body); bound = historical
        for key in keys: bound = bound[key]
        same(bound, files[name]['sha256'], 'historical executable binding')
        sources = historical.get('source_sha256', historical.get('sources'))
        if isinstance(sources, list): sources = {r['path']: r['sha256'] for r in sources}
        need(isinstance(sources, dict) and sources, 'source bindings absent')
        for source, sha in sources.items(): same(digest(smoke.blob(repo, pin, source)), sha, 'pinned source blob')
        provenance.append(dict(artifact=name, commit=pin, receipt=PREFIX+rel,
                               receipt_sha256=digest(body), source_bindings=len(sources), fresh_build=False))
    (rrf, rrb), bindings = manifests(repo)
    # Compare fresh queried capabilities against the exact prior successful smoke.
    # A changed device/driver/memory model calls for explicit re-review.
    for label in ('memory', 'extensions'):
        prior = smoke.blob(repo, SMOKE_PIN, PREFIX+'headset_review_smoke/results/device-run/'+label+'.stdout')
        same(parse(outputs[label]), parse(prior), 'capability/device drift')
    reports = []
    memory_device = parse(outputs['memory'])['devices'][0]
    memory_types = {row['index']: row['flags'] for row in memory_device['memory_types']}
    for label in LABELS:
        data = parse(outputs[label])
        for case in data['cases']:
            same(case['device'], memory_device['device'], 'queried GPU identity')
            same(case['device_type'], memory_device['type'], 'queried GPU type')
            same(case['driver'], memory_device['driver'], 'queried driver')
            for row in case['iterations']:
                same(row['input_memory_flags'], [memory_types.get(i) for i in row['input_memory_type']],
                     'queried actual memory flags')
        reports.append(dict(label=label, result_sha256=digest(outputs[label]),
                            process_elapsed_s=elapsed[label], **analyze(data, rrf, rrb)))
    return dict(schema=1, status='pass', headset_execution=True, exploratory_comparison=True,
                tail_qualification=False, native_renderer_tested=False, sub15_qualification=False,
                promotion_eligible=False, plan_sha256=digest(raw_plan), receipt_sha256=digest(raw_receipt),
                runner_sha256=smoke.RUNNER_SHA, audit_sha256=digest(Path(__file__).read_bytes()),
                raw_log_sha256=logs, source_build_provenance=provenance, corpus_manifest_sha256=bindings,
                audit_helper_bindings=helper_bindings,
                firmware=outputs['fingerprint'].decode().strip(), repeat_reports=reports,
                valid_invocations_including_warmups=360, recorded_invocations=252, omission_controls=36,
                limitations=[
                    'Seven observations per arm per process are exploratory; no p95/tail or population qualification.',
                    'Two fixed repeated captured pairs: one Q1 and one Q8; no corpus-wide or live-scene inference.',
                    'Raw portable probe labels remain host-only; wrapper serial/argv/upload hashes establish Android execution.',
                    'Three synchronous submissions: poison, readiness and first private compute consumer. Not native rendering.',
                    'Oracle, mapped-float snapshots, JSON output and setup are outside per-invocation clocks but perturb later work.',
                    'Default and cached requested arms can choose identical memory; actual selections are reported.',
                    'Device clocks cover queued GPU spans; CPU wall includes driver/scheduling/fence delays, without assigning cause.',
                    'Thermal, charging, OS, display and radio activity are uncontrolled unless separately recorded.',
                    'Source/binary bindings are historical; no fresh build, authenticity proof, wire/network, source age or optical claim.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('plan', 'audit')); p.add_argument('--repo', type=Path, default=ROOT)
    p.add_argument('--plan', type=Path); p.add_argument('--run-directory', type=Path)
    p.add_argument('--input-root', type=Path); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    value = plan_for(a.repo) if a.command == 'plan' else audit(a.plan, a.run_directory, a.repo, a.input_root)
    with a.out.open('x') as f: json.dump(value, f, indent=2, allow_nan=False); f.write('\n')
    print(json.dumps(dict(output=str(a.out), command=a.command)))


if __name__ == '__main__': main()
