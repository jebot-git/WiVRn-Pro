#!/usr/bin/env python3
"""Acquire a pinned native selector campaign; keep failed runs and verify restoration."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'preflight').mkdir()
    adb = ['adb', '-s', plan['adb_target_last_observed']]
    package = 'org.meumeu.wivrn.codectest'
    status = {'schema': 1, 'status': 'preflight', 'plan_sha256': sha(args.plan),
              'runner_sha256': sha(__file__), 'runs': [], 'commands': [],
              'headset_used': True, 'promotion_eligible': False}

    def save():
        (out / 'campaign.json').write_text(json.dumps(status, indent=2) + '\n')

    def capture(label, argv, allowed=(0,)):
        result = subprocess.run(argv, capture_output=True, timeout=30)
        for suffix, data in [('stdout', result.stdout), ('stderr', result.stderr)]:
            (out / 'preflight' / (label + '.' + suffix)).write_bytes(data)
        status['commands'].append({'label': label, 'argv': argv, 'returncode': result.returncode,
                                   'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
                                   'stderr_sha256': hashlib.sha256(result.stderr).hexdigest()})
        save()
        if result.returncode not in allowed:
            raise RuntimeError(label + ' failed')
        return result.stdout

    def configs():
        result = {}
        for name in plan['config_backup_restore']:
            command = subprocess.run(adb + ['exec-out', 'run-as', package, 'cat', 'files/' + name],
                                     capture_output=True, timeout=30)
            if command.returncode == 0:
                result[name] = {'bytes': len(command.stdout), 'sha256': hashlib.sha256(command.stdout).hexdigest()}
            elif b'No such file' in command.stderr or b'No such file' in command.stdout:
                result[name] = None
            else:
                raise RuntimeError('cannot verify config: ' + name)
        return result

    def source_hashes():
        root = Path(plan['harness']).parents[3]
        paths = list((root / 'validation/wivrn-codecs').rglob('*.py'))
        paths.append(root / 'prototypes/vr444/experiments/compositor_freshness/analyze.py')
        return {str(p.relative_to(root)): sha(p) for p in sorted(paths)}

    def check_build():
        receipt = json.loads(Path(plan['build_receipt']).read_text())
        if sha(plan['build_receipt']) != plan['build_receipt_sha256']:
            raise RuntimeError('build receipt changed')
        mismatches = [name for name, digest in receipt['sha256'].items()
                      if sha(Path(plan['source']) / name) != digest]
        if mismatches:
            raise RuntimeError('build drift: ' + ', '.join(mismatches))
        return len(receipt['sha256'])

    try:
        if sha(plan['harness']) != plan['harness_sha256']:
            raise RuntimeError('harness changed')
        if sha(Path(plan['harness']).with_name('candidate_trace.py')) != plan['analyzer_sha256']:
            raise RuntimeError('analyzer changed')
        status['build_files_verified'] = check_build()
        status['analysis_source_sha256_before'] = source_hashes()
        serial = capture('serial', adb + ['shell', 'getprop', 'ro.serialno']).decode().strip()
        if serial != plan['expected_physical_serial']:
            raise RuntimeError('wrong physical headset')
        capture('fingerprint', adb + ['shell', 'getprop', 'ro.build.fingerprint'])
        capture('battery-before', adb + ['shell', 'dumpsys', 'battery'])
        capture('thermal-before', adb + ['shell', 'dumpsys', 'thermalservice'])
        capture('power-before', adb + ['shell', 'dumpsys', 'power'])
        capture('reverse-before', adb + ['reverse', '--list'])
        running = capture('test-app-before', adb + ['shell', 'pidof', package], allowed=(0, 1)).strip()
        if running:
            raise RuntimeError('dedicated test app already active; preserve it')
        production = capture('production-before', adb + ['shell', 'dumpsys', 'package', 'org.meumeu.wivrn'])
        initial = configs()
        status['initial_config_sha256'] = initial
        # Preserve the previous dedicated capture before the native harness replaces it.
        capture('previous-capture', adb + ['exec-out', 'run-as', package, 'tar', '-C', 'files', '-cf', '-',
                                         'codec-test', 'codec-test-present.jsonl'], allowed=(0, 1))
        for arm in plan['runs']:
            argv = [word.replace('{fresh_output_root}', str(out)) for word in arm['argv']]
            record = {'name': arm['name'], 'argv': argv, 'start_utc': datetime.now(timezone.utc).isoformat()}
            status['runs'].append(record)
            status['status'] = 'running'
            save()
            print('START ' + arm['name'], flush=True)
            with (out / (arm['name'] + '-runner.log')).open('wb') as log:
                # Individual acquisition operations and its child processes are bounded.
                # An outer kill could preempt the acquisition's finally/restoration block.
                process = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT)
            record['returncode'] = process.returncode
            record['finish_utc'] = datetime.now(timezone.utc).isoformat()
            folder = out / arm['name']
            summary_path, cleanup_path = folder / 'summary.json', folder / 'cleanup.json'
            summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
            cleanup = json.loads(cleanup_path.read_text()) if cleanup_path.exists() else {}
            record['evidence_status'] = summary.get('status')
            record['cleanup'] = cleanup
            record['config_readback_matches_initial'] = configs() == initial
            record['summary_sha256'] = sha(summary_path) if summary_path.exists() else None
            record['battery'] = capture(arm['name'] + '-battery', adb + ['shell', 'dumpsys', 'battery']).decode()
            save()
            print(json.dumps({'name': arm['name'], 'status': record['evidence_status'],
                              'config_restored': record['config_readback_matches_initial']}), flush=True)
            if process.returncode or summary.get('status') != 'pass' or cleanup.get('restored') is not True \
                    or cleanup.get('cleanup_errors') or not record['config_readback_matches_initial']:
                raise RuntimeError('stop on evidence or cleanup failure: ' + arm['name'])
        status['status'] = 'complete'
    except Exception as error:
        status['status'] = 'stopped'
        status['error'] = str(error)
        raise
    finally:
        errors = []
        def attempt(label, action):
            try:
                return action()
            except Exception as error:
                errors.append(label + ': ' + str(error))
                return None
        status['analysis_source_sha256_after'] = attempt('source hashes', source_hashes)
        status['analysis_sources_unchanged'] = status.get('analysis_source_sha256_before') is not None \
            and status.get('analysis_source_sha256_before') == status['analysis_source_sha256_after']
        status['build_files_verified_after'] = attempt('build hashes', check_build)
        status['final_config_sha256'] = attempt('config readback', configs)
        status['final_config_matches_initial'] = status.get('initial_config_sha256') is not None \
            and status.get('initial_config_sha256') == status['final_config_sha256']
        attempt('battery', lambda: capture('battery-after', adb + ['shell', 'dumpsys', 'battery']))
        attempt('thermal', lambda: capture('thermal-after', adb + ['shell', 'dumpsys', 'thermalservice']))
        reverse = attempt('reverse', lambda: capture('reverse-after', adb + ['reverse', '--list']))
        pid = attempt('app pid', lambda: capture('test-app-after', adb + ['shell', 'pidof', package], allowed=(0, 1)))
        final_production = attempt('production package', lambda: capture('production-after', adb + ['shell', 'dumpsys', 'package', 'org.meumeu.wivrn']))
        status['own_reverse_absent'] = reverse is not None and b'tcp:9767' not in reverse
        status['dedicated_app_stopped'] = pid is not None and not pid.strip()
        status['production_package_unchanged'] = 'production' in locals() and final_production == production
        status['final_check_errors'] = errors
        required = ('analysis_sources_unchanged', 'final_config_matches_initial',
                    'own_reverse_absent', 'dedicated_app_stopped', 'production_package_unchanged')
        if status['status'] == 'complete' and (errors or not all(status[name] is True for name in required)):
            status['status'] = 'failed_postconditions'
        status['finish_utc'] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({'status': status['status'], 'campaign': str(out / 'campaign.json')}), flush=True)
        if status['status'] == 'failed_postconditions':
            raise RuntimeError('postcondition verification failed')


if __name__ == '__main__':
    main()
