#!/usr/bin/env python3
"""Audit the fixed Android timeline/sync2 execution; no device or network calls."""
import argparse
from datetime import datetime
import importlib.util
import json
import math
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PREFIX = 'prototypes/vr444/experiments/'
PIN = '00939ca2c504a525e69c173221e1afb67b5be6bc'
SMOKE_PIN = '146bab8d10a9e34ca3a78b6fc6634639287a3888'
RECEIPT_PATH = PREFIX+'vulkan_extensions/qualification-20261009.json'
PLAN_SHA = 'cec0acdd528a30d5442f35d3630f154592d7ca67dbf561a269bea25f9951e4f9'
BINARY_SHA = '76a2e8d56cb7cb90c798f9375515962bee0a0acd3336acf45a97ea52bcd5647e'
SPEC = importlib.util.spec_from_file_location('timeline_smoke_helpers', HERE.parent/'headset_review_smoke/audit.py')
smoke = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(smoke)
need, same, digest, parse = smoke.need, smoke.same, smoke.digest, smoke.parse


def accounting(data, modern):
    for key, value in dict(schema=1, pass_=True, device='Adreno (TM) 650', timeline_enabled=True,
                           synchronization2_enabled=modern, slots=3, offered_pairs=512,
                           headset_used=False, performance_claim=False,
                           scope='Private queue buffer identity/lifetime model; no images, codec, XR or presentation latency').items():
        same(data.get('pass' if key == 'pass_' else key), value, 'probe scope '+key)
    counters = ('decoded_pairs','consumed_pairs','verified_pairs','pressure_drops',
                'unacquired_completed_drops','slot_reuses')
    for key in counters:
        need(type(data.get(key)) is int and 0 <= data[key] <= 512, 'bounded integer counter '+key)
    d, c, v, p, u, r = [data[key] for key in counters]
    need(d+p == 512 and c+u == d, 'every offered/admitted pair accounted')
    need(c == v and c >= 3 and r >= 3 and p > 0, 'retirement/readback/pressure coverage')
    # The first producer burst fills all three slots before observing completion.
    need(r == d-3, 'three initial allocations plus every subsequent slot generation')
    return dict(submit_path='vkQueueSubmit2KHR' if modern else 'vkQueueSubmit',
                timeline_enabled_and_used=True, synchronization2_enabled_and_used=modern,
                slots=3, bytes_per_eye=4096, offered_pairs=512,
                **{key:data[key] for key in counters}, accounting_exact=True,
                scope='Synthetic stereo transfer buffers on one owned private Vulkan queue only.')


def audit(plan_path, directory, repo, binary=None):
    raw_plan, raw_receipt = plan_path.read_bytes(), (directory/'receipt.json').read_bytes()
    same(digest(raw_plan), PLAN_SHA, 'fixed plan')
    plan, receipt = parse(raw_plan), parse(raw_receipt)
    helper_rel = PREFIX+'headset_review_smoke/audit.py'
    helper_sha = digest(smoke.blob(repo, SMOKE_PIN, helper_rel))
    same(digest((HERE.parent/'headset_review_smoke/audit.py').read_bytes()), helper_sha, 'pinned audit helper')
    same(digest((HERE.parent/'headset_review_smoke/run.py').read_bytes()), smoke.RUNNER_SHA, 'unchanged bounded runner')
    same(receipt['plan_sha256'], PLAN_SHA, 'receipt plan binding')
    same(receipt['runner_sha256'], smoke.RUNNER_SHA, 'receipt runner binding')
    for key, value in dict(schema=1, status='completed', device_serial=smoke.SERIAL,
                           headset_used=True, root_required=False, openxr_used=False,
                           performance_qualification=False, promotion_eligible=False, inputs_unchanged=True).items():
        same(receipt.get(key), value, 'execution '+key)
    need(datetime.fromisoformat(receipt['finish_utc']) > datetime.fromisoformat(receipt['start_utc']), 'UTC interval')
    same(receipt['inputs'], plan['files'], 'input population')
    same(len(plan['files']), 1, 'single executable input')
    item = plan['files'][0]
    same(item['remote'], 'timeline-probe', 'executable name'); same(item['executable'], True, 'executable mode')
    same(item['sha256'], BINARY_SHA, 'executable pin')
    same(plan['source_receipt_commit'], PIN, 'source commit'); same(plan['source_receipt_path'], RECEIPT_PATH, 'source receipt')
    binary = Path(item['local']) if binary is None else binary
    same(digest(binary.read_bytes()), BINARY_SHA, 'retained executable')
    remote = receipt['remote_directory']
    need(re.fullmatch('/data/local/tmp/sub15-review-[0-9a-f]{32}', remote), 'owned directory')
    expected = smoke.expected_commands(plan, remote)
    need(len(receipt['commands']) == len(expected) == 12, 'complete command population')
    outputs, logs = {}, {}
    for entry, (label, argv, timeout) in zip(receipt['commands'], expected, strict=True):
        same(entry['label'], label, 'command order'); same(entry['argv'], argv, 'exact argv')
        same(entry['timeout_s'], timeout, 'timeout bound'); same(entry.get('returncode'), 0, 'successful command')
        same(entry.get('host_timeout', False), False, 'no host timeout')
        need(type(entry['elapsed_s']) in (int, float) and math.isfinite(entry['elapsed_s']) and entry['elapsed_s'] >= 0,
             'finite host invocation duration')
        for suffix in ('stdout', 'stderr'):
            name = label+'.'+suffix; raw = (directory/name).read_bytes()
            same(digest(raw), entry[suffix+'_sha256'], 'log bytes binding')
            logs[name] = digest(raw)
            if suffix == 'stdout': outputs[label] = raw
            elif label != 'push-0': need(not raw, 'unexpected stderr')
    same(outputs['serial'].decode().strip(), smoke.SERIAL, 'actual headset identity')
    need(outputs['fingerprint'].decode().strip(), 'actual firmware identity')
    same(outputs['hash-0'].decode().strip().split(), [BINARY_SHA, remote+'/timeline-probe'], 'uploaded executable')
    source_raw = smoke.blob(repo, PIN, RECEIPT_PATH); source = parse(source_raw)
    same(source['binaries'][3]['path'], PREFIX+'vulkan_extensions/build/timeline_probe-android', 'historical artifact identity')
    same(source['binaries'][3]['sha256'], BINARY_SHA, 'historical executable binding')
    same(source['binaries'][3]['bytes'], binary.stat().st_size, 'historical executable size')
    sources = source.get('source_sha256', source.get('sources'))
    if isinstance(sources, list): sources = {row['path']: row['sha256'] for row in sources}
    need(isinstance(sources, dict) and sources, 'historical source bindings')
    for path, sha in sources.items(): same(digest(smoke.blob(repo, PIN, path)), sha, 'source binding '+path)
    results = [accounting(parse(outputs[label]), modern)
               for label, modern in (('timeline-legacy', False), ('timeline-sync2', True))]
    return dict(schema=1, status='pass', headset_execution=True, actual_feature_execution=True,
                private_queue_buffer_lifetime_compatible=True, native_lifetime_qualified=False,
                image_or_codec_tested=False, performance_claim=False, sub15_qualification=False,
                promotion_eligible=False, plan_sha256=PLAN_SHA, receipt_sha256=digest(raw_receipt),
                runner_sha256=smoke.RUNNER_SHA, audit_sha256=digest(Path(__file__).read_bytes()),
                helper_binding=dict(commit=SMOKE_PIN, path=helper_rel, sha256=helper_sha),
                raw_log_sha256=logs, binary_sha256=BINARY_SHA, firmware=outputs['fingerprint'].decode().strip(),
                build_provenance=dict(commit=PIN, receipt=RECEIPT_PATH, receipt_sha256=digest(source_raw),
                                      source_bindings_verified=len(sources), fresh_build=False),
                results=results, owned_directory_removal_verified=True,
                limitations=[
                    'Artificial 512-offer burst schedule and three private slots: counts are not useful streaming throughput.',
                    'Exact 4096-byte-per-eye transfer buffers; no decoder, images, native frame handles, compositor or XR.',
                    'No latency comparison or speedup follows from either successful extension path.',
                    'Raw probe retains headset_used:false; immutable Android wrapper supplies execution provenance.',
                    'The 15-second internal loop is backed by 30-second device KILL and 45-second host limits; device drain alone is not bounded.',
                    'Directory cleanup is observed; this auditor does not independently inspect device processes.',
                    'Binary/source bindings are historical, not a fresh build or authenticity proof.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan', type=Path); p.add_argument('directory', type=Path)
    p.add_argument('--repo', type=Path, default=ROOT); p.add_argument('--binary', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); result = audit(a.plan, a.directory, a.repo, a.binary)
    with a.out.open('x') as f: json.dump(result, f, indent=2, allow_nan=False); f.write('\n')
    print(json.dumps(dict(status=result['status'], output=str(a.out))))


if __name__ == '__main__': main()
