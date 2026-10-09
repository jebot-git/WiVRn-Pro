#!/usr/bin/env python3
"""Read-only audit of the fixed 2026-10-09 standalone Quest smoke; never invokes ADB."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
PLAN_SHA = 'ae32c2cf89256061da4ab1a7a951f82de89f25aff057ee822020d4dcbf9e8b3a'
RUNNER_SHA = 'e495081a5bdbbef3aa94cff0082b700516740dca711d10d3ec9dedb143e4c64a'
SERIAL = '230YC01D8W000E'
GPU_PIN = 'c4dc09128e45fc5bda71fa0588e40d2077678b9b'
PREFIX = 'prototypes/vr444/experiments/'
ARMS = ['rrf_cpu_reference', 'rrf_staged_default', 'rrf_direct_default',
        'rrf_staged_cached', 'rrf_direct_cached', 'rrb32_staged_default',
        'rrb32_direct_default', 'rrb32_staged_cached', 'rrb32_direct_cached']
PROFILES = {'corpus/lossless/360': 1, 'corpus/paired-a/1350': 8}
# Every binding names its own immutable receipt commit, not the plan's GPU context.
BINDINGS = [
    ('memory-capabilities', GPU_PIN, 'gpu_qualification_suite/qualification-final-20261009.json',
     ['fresh_execution', 'built_artifact_sha256', 'build/capabilities-android']),
    ('mapped-consumer', GPU_PIN, 'gpu_qualification_suite/qualification-final-20261009.json',
     ['fresh_execution', 'consumer_built_artifact_sha256', 'consumer-build/mapped-consumer-android']),
    ('extension-capabilities', '00939ca2c504a525e69c173221e1afb67b5be6bc',
     'vulkan_extensions/qualification-20261009.json', ['binaries', 0, 'sha256']),
    ('band-views-test', 'a55dff163e3defa5d5be2e600b8d9a7829a8f7a1',
     'rowblocks_band_views/results/qualification.json', ['binary_sha256', 'test-android']),
    ('simd-test', '895e9eff0bb0423f78ce95eae4ba0c1ea1dbaaf8',
     'rgb_rowfilter/simd-results/measurement-provenance.json', ['binary_sha256', 'test-android']),
]


def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def bad(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)


def same(actual, expected, message):
    # JSON canonicalization distinguishes true from 1, including nested objects.
    need(json.dumps(actual, sort_keys=True, allow_nan=False) ==
         json.dumps(expected, sort_keys=True, allow_nan=False), message)


def blob(repo, commit, path):
    return subprocess.run(['git', '--no-replace-objects', '-C', str(repo),
                           'show', commit + ':' + path], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def expected_commands(plan, remote):
    adb = ['adb', '-s', plan['adb_target']]
    commands = []
    def shell(label, argv, timeout=30):
        commands.append((label, adb + ['shell', shlex.join(argv)], timeout))
    shell('serial', ['getprop', 'ro.serialno'])
    shell('fingerprint', ['getprop', 'ro.build.fingerprint'])
    shell('battery-before', ['dumpsys', 'battery'])
    shell('mkdir', ['mkdir', remote])
    parents = set()
    for i, item in enumerate(plan['files']):
        dest = remote + '/' + item['remote']
        parent = str(PurePosixPath(dest).parent)
        if parent != remote and parent not in parents:
            shell('mkdir-' + str(i), ['mkdir', '-p', parent])
            parents.add(parent)
        commands.append(('push-' + str(i), adb + ['push', item['local'], dest], 60))
        shell('hash-' + str(i), ['sha256sum', dest])
        if item.get('executable'):
            shell('chmod-' + str(i), ['chmod', '700', dest])
    for run in plan['runs']:
        shell(run['name'], ['timeout', '-s', 'KILL', str(run['timeout_s'])] +
              [x.replace('{remote}', remote) for x in run['argv']], run['timeout_s'] + 15)
    shell('battery-after', ['dumpsys', 'battery'])
    shell('cleanup', ['rm', '-rf', remote])
    shell('cleanup-verified', ['test', '!', '-e', remote])
    return commands


def audit_gpu(data, memory, extensions, files, corpus, input_paths):
    for key, value in {'schema': 1, 'experiment': 'mapped-input-consumer',
                       'qualification_only': False, 'headset_used': False,
                       'promotion_blocked': True, 'warmups': 3, 'repetitions': 1,
                       'cpu_backend': 'aarch64-neon', 'memory_gate_rejections': 5,
                       'case_count': 2, 'pass': True,
                       'checked_crc_grammar_lz4_in_every_arm': True,
                       'poison_in_total_time': True, 'oracle_outside_total_time': True,
                       'reused_workspaces': True}.items():
        same(data.get(key), value, 'GPU scope: ' + key)
    for value in (memory, extensions):
        same(value.get('schema'), 1, 'capability schema')
        same(value.get('physical_query_only'), True, 'physical capability query')
        same(value.get('logical_device_created'), False, 'query cannot imply enabled extension')
        need(len(value['devices']) == 1, 'expected one physical GPU')
        same(value['devices'][0]['index'], 0, 'device index')
    m, e = memory['devices'][0], extensions['devices'][0]
    need(m['device'] == e['name'] == 'Adreno (TM) 650', 'capability device join')
    same(m['driver'], e['driver_version'], 'driver join')
    memory_flags = {x['index']: x['flags'] for x in m['memory_types']}
    need(len(memory_flags) == len(m['memory_types']), 'duplicate memory type')
    cases = data['cases']
    need(len(cases) == 2 and {x['name'] for x in cases} == set(PROFILES), 'two exact corpus profiles')
    corpus_rows = {r['rrf']: r for r in corpus['cases']}
    flags = ['first_consumer_saved_before_oracle', 'first_consumer_samples_exact',
             'right_consumer_omission_rejected', 'image_bytes_exact',
             'sampled_cpu_copy_baseline_exact', 'malformed_pair_rejected_before_write_or_submit',
             'wrong_shape_rejected_before_write_or_submit']
    summary = []
    for case in cases:
        same([case['width'], case['height']], [576, 640], 'profile dimensions')
        same(case['synthetic_duplicated_eyes'], False, 'real separate captured eyes')
        need(case['device'] == m['device'] and case['driver'] == m['driver'], 'case physical device')
        stem = case['name'].removeprefix('corpus/')
        for eye in (0, 1):
            name = stem + '-' + str(eye) + '.rrf'
            row = corpus_rows[name]
            same(row['q'], PROFILES[case['name']], 'corpus Q label')
            same(row['exact'], True, 'corpus exact reference')
            for kind in ('rrf', 'rgba'):
                need(files['rrf/' + row[kind]]['sha256'] == row[kind + '_sha256'], 'corpus input hash')
        variants = case['variants']
        same([x['variant'] for x in variants], [0, 2], 'RRF/RRB32 variants')
        for v in variants:
            suffix = '.rrf' if v['variant'] == 0 else '.r32.rrb'
            folder = 'rrf/' if v['variant'] == 0 else 'rrb/'
            same(v['eye_wire_bytes'], [input_paths[folder + stem + '-' + str(i) + suffix].stat().st_size
                                      for i in (0, 1)], 'variant actual bytes')
            same(v['eye_block_rows'], [0, 0] if v['variant'] == 0 else [32, 32], 'block rows')
            same(v['eye_block_counts'], [1, 1] if v['variant'] == 0 else [20, 20], 'block counts')
            same(v['compressed_present'], True, 'compressed fault gate population')
        rows = case['iterations']
        need(len(rows) == 9 and {x['mode'] for x in rows} == set(ARMS), 'all nine modes exactly once')
        same([x['order'] for x in rows], list(range(9)), 'complete arm order')
        for row in rows:
            mode = row['mode']
            for key in flags:
                same(row.get(key), True, 'GPU exactness/control: ' + key)
            same(row['rep'], 0, 'one recorded repetition')
            same(row['variant'], 2 if mode.startswith('rrb32') else 0, 'arm source variant')
            same(row['cached_requested'], mode.endswith('_cached'), 'cache request')
            same(row['late_decoder_gate_exercised'], '_direct_' in mode, 'late decoder fault control')
            same([row['timed_queue_submissions'], row['first_consumer_submissions'],
                  row['joint_queue_submissions']], [2, 1, 3], 'submission counts')
            need(len(row['input_memory_type']) == len(row['input_memory_flags']) == 2, 'both eye memories')
            for index, flags_value in zip(row['input_memory_type'], row['input_memory_flags']):
                same(flags_value, memory_flags[index], 'actual input memory type flags')
                required = 14 if row['cached_requested'] else 6
                need(flags_value & required == required, 'compatible coherent/cached memory')
            need(type(row['max_srgb_code_error']) in (int, float) and
                 math.isfinite(row['max_srgb_code_error']) and 0 <= row['max_srgb_code_error'] <= 1,
                 'bounded SRGB error')
            for key, value in row.items():
                if key.endswith('_ms'):
                    need(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                         'finite diagnostic clock')
        summary.append({'name': case['name'], 'q': PROFILES[case['name']], 'modes': ARMS,
                        'recorded_exact_stereo_invocations': 9, 'warmups_per_arm': 3,
                        'right_eye_omission_controls': 9})
    return summary


def audit(plan_path, run_dir, repo, input_root=None):
    plan_raw = plan_path.read_bytes()
    need(digest(plan_raw) == PLAN_SHA, 'different executed plan')
    plan = parse(plan_raw)
    raw = (run_dir / 'receipt.json').read_bytes()
    receipt = parse(raw)
    need(digest((HERE / 'run.py').read_bytes()) == RUNNER_SHA, 'executed runner changed')
    same(receipt['plan_sha256'], PLAN_SHA, 'receipt plan binding')
    same(receipt['runner_sha256'], RUNNER_SHA, 'receipt runner binding')
    for key, value in {'schema': 1, 'status': 'completed', 'device_serial': SERIAL,
                       'headset_used': True, 'inputs_unchanged': True, 'root_required': False,
                       'openxr_used': False, 'performance_qualification': False,
                       'promotion_eligible': False}.items():
        same(receipt.get(key), value, 'receipt: ' + key)
    need(datetime.fromisoformat(receipt['finish_utc']) > datetime.fromisoformat(receipt['start_utc']), 'run time order')
    same(receipt['inputs'], plan['files'], 'exact input population')
    remote = receipt['remote_directory']
    need(re.fullmatch('/data/local/tmp/sub15-review-[0-9a-f]{32}', remote), 'owned remote directory')
    commands = receipt['commands']
    expected = expected_commands(plan, remote)
    need(len(commands) == len(expected) == 105, 'exact full command count')
    logs, outputs = {}, {}
    for entry, (label, argv, timeout) in zip(commands, expected):
        same(entry['label'], label, 'command order')
        same(entry['argv'], argv, 'exact command argv')
        same(entry['timeout_s'], timeout, 'timeout bound')
        same(entry.get('returncode'), 0, 'command failed: ' + label)
        need(entry.get('host_timeout', False) is False, 'host timeout: ' + label)
        for suffix in ('stdout', 'stderr'):
            name = label + '.' + suffix
            data = (run_dir / name).read_bytes()
            need(digest(data) == entry[suffix + '_sha256'], 'raw log changed: ' + name)
            logs[name] = digest(data)
            if suffix == 'stdout':
                outputs[label] = data
            elif not label.startswith('push-'):
                need(not data, 'unexpected diagnostics: ' + label)
    need(outputs['serial'].decode().strip() == plan['expected_serial'] == SERIAL, 'actual headset identity')
    need(outputs['fingerprint'].decode().strip(), 'build fingerprint absent')
    files = {x['remote']: x for x in plan['files']}
    need(len(files) == len(plan['files']) == 41, 'exact input population')
    input_paths = {name: input_root / name if input_root is not None else Path(item['local'])
                   for name, item in files.items()}
    for i, item in enumerate(plan['files']):
        need(digest(input_paths[item['remote']].read_bytes()) == item['sha256'], 'retained input changed')
        same(outputs['hash-' + str(i)].decode().strip().split(),
             [item['sha256'], remote + '/' + item['remote']], 'device hash binding')
    simd = parse(outputs['simd'])
    same(simd, {'pass': True, 'roundtrips': 271, 'rejections': 8422, 'mutations': 10000,
                'maximum_pixels': 4194304, 'output_unchanged_on_rejection': True,
                'independent_modulo_predictor_oracle': True, 'scalar_simd_canonical_wire_equal': True,
                'simd_boundary_cases': 65, 'backend': 'aarch64-neon'}, 'complete Android SIMD output')
    band = parse(outputs['band-views'])
    same(band, {'pass': True, 'exact_canonical_stereo_cases': 733, 'direct_rgb_oracle_cases': 576,
                'differential_corruption_cases': 2000, 'rejections': 6248, 'worker_counts': [1, 2, 4],
                'reuse_batches': 300, 'max_block_jobs': 512, 'max_pixels_per_eye': 4194304,
                'source_qmaps_and_odd_dimensions': True, 'separate_published_lease_retained': True},
         'complete Android band-view output')
    provenance = []
    for name, pin, rel, keys in BINDINGS:
        path = PREFIX + rel
        body = blob(repo, pin, path)
        historical = parse(body)
        bound = historical
        for key in keys:
            bound = bound[key]
        need(bound == files[name]['sha256'], 'Android executable differs from original build receipt')
        sources = historical.get('source_sha256', historical.get('sources'))
        if isinstance(sources, list):
            sources = {x['path']: x['sha256'] for x in sources}
        need(isinstance(sources, dict) and sources, 'original source bindings absent')
        for source_path, source_sha in sources.items():
            need(digest(blob(repo, pin, source_path)) == source_sha, 'historical source mismatch: ' + source_path)
        provenance.append({'remote_artifact': name, 'binary_sha256': bound,
                           'receipt_commit': pin, 'receipt_path': path,
                           'receipt_sha256': digest(body), 'binary_binding_keys': keys,
                           'verified_repository_source_bindings': len(sources),
                           'fresh_build': False})
    corpus_path = PREFIX + 'rgb_rowfilter/results/corpus-hashes.json'
    corpus_raw = blob(repo, GPU_PIN, corpus_path)
    gpu = parse(outputs['gpu-two-profiles'])
    memory, extensions = parse(outputs['memory']), parse(outputs['extensions'])
    profiles = audit_gpu(gpu, memory, extensions, files, parse(corpus_raw), input_paths)
    return {'schema': 1, 'status': 'pass', 'audit_sha256': digest(Path(__file__).read_bytes()),
            'plan_sha256': PLAN_SHA, 'runner_sha256': RUNNER_SHA, 'receipt_sha256': digest(raw),
            'raw_directory': str(run_dir.resolve()), 'raw_log_sha256': logs,
            'input_sha256': {k: v['sha256'] for k, v in files.items()},
            'command_count': len(commands), 'all_commands_exit_zero': True,
            'no_host_timeouts': True, 'runner_recorded_local_inputs_unchanged': True,
            'retained_inputs_reverified': True,
            'input_location': str(input_root.resolve()) if input_root is not None else 'original plan paths',
            'serial': SERIAL, 'build_fingerprint': outputs['fingerprint'].decode().strip(),
            'owned_directory_removal_verified': True, 'process_absence_independently_verified': False,
            'cpu': {'simd': simd, 'band_views': band}, 'gpu_profiles': profiles,
            'gpu_device': memory['devices'][0], 'extension_query': extensions['devices'][0],
            'build_provenance': provenance,
            'corpus_provenance': {'commit': GPU_PIN, 'path': corpus_path, 'sha256': digest(corpus_raw)},
            'headset_execution': True, 'root_used_by_runner': False, 'openxr_qualification': False,
            'comparative_timing': False, 'sub15_qualification': False, 'promotion_eligible': False,
            'scope': ['Fixed two-profile standalone capability/correctness smoke only.',
                      'Raw portable probes retain historical host-only labels; actual device execution is established by wrapper serial, argv and upload hashes.',
                      'One recorded repetition per arm; retained diagnostic clocks are not comparative timing evidence.',
                      'Extension advertisement/query is not extension enablement; the private image consumer is not the native renderer.',
                      'Existing Android binaries are linked to immutable historical receipts and source blobs; no fresh or reproducible build is claimed.',
                      'Offline replay requires the pinned Git objects and retained binaries/shaders/corpora; missing external inputs fail closed.',
                      'Directory absence is checked; process absence and optical/display/OpenXR behavior are not independently checked.',
                      'Android band-view rejection count is 6248; historical host sanitizer count 6256 is not relabeled as device execution.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--repo', type=Path, default=HERE.parents[3])
    parser.add_argument('--input-root', type=Path,
                        help='Retained inputs under their remote-relative paths; default: original plan paths')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.plan, args.run_directory, args.repo, args.input_root)
    with args.out.open('x') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'commands': report['command_count'],
                      'gpu_recorded_invocations': 18, 'audit': str(args.out)}))


if __name__ == '__main__':
    main()
