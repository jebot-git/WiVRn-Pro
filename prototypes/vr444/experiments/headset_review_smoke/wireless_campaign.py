#!/usr/bin/env python3
"""Run finite explicit-peer LAN probes; root owns both endpoints and evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid


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
    remote = '/data/local/tmp/sub15-wireless-' + uuid.uuid4().hex
    adb = ['adb', '-s', plan['adb_target']]
    audit = Path(plan['source_directory']) / 'audit_network.py'
    receipt = {'schema': 1, 'status': 'preflight', 'start_utc': datetime.now(timezone.utc).isoformat(),
               'plan_sha256': sha(args.plan), 'runner_sha256': sha(__file__),
               'audit_sha256': sha(audit), 'commands': [], 'runs': [], 'remote_directory': remote,
               'headset_used': True, 'openxr_used': False, 'promotion_eligible': False,
               'scope': 'Bounded actual LAN synthetic-pair diagnostic; one outstanding pair; no cross-clock one-way or physical-wire measurement.'}
    port_pattern = re.compile(r':(?:' + '|'.join(str(x) for x in plan['require_free_receiver_ports']) + r')(?:\s|$)')

    def save():
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

    def finish(entry, result):
        entry['returncode'] = result
        for suffix in ('stdout', 'stderr'):
            entry[suffix + '_sha256'] = sha(out / (entry['label'] + '.' + suffix))
        save()

    def command(label, argv, timeout=30, checked=True):
        entry = {'label': label, 'argv': argv, 'timeout_s': timeout}
        receipt['commands'].append(entry)
        with (out / (label + '.stdout')).open('wb') as stdout, (out / (label + '.stderr')).open('wb') as stderr:
            result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=timeout)
        finish(entry, result.returncode)
        if checked and result.returncode:
            raise RuntimeError(label + ' failed')
        return (out / (label + '.stdout')).read_bytes()

    def shell(label, argv, **kw):
        return command(label, adb + ['shell', shlex.join(argv)], **kw)

    def endpoint(label, argv, timeout):
        entry = {'label': label, 'argv': argv, 'timeout_s': timeout}
        receipt['commands'].append(entry)
        stdout = (out / (label + '.stdout')).open('wb')
        stderr = (out / (label + '.stderr')).open('wb')
        deadline = time.monotonic() + timeout
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
        return process, entry, stdout, stderr, deadline

    def wait_endpoint(state, timeout):
        process, entry, stdout, stderr, deadline = state
        try:
            code = process.wait(timeout=max(.001, min(timeout, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            entry['host_timeout'] = True
            process.kill()
            code = process.wait()
        finally:
            stdout.close()
            stderr.close()
        finish(entry, code)
        return code

    try:
        if sha(plan['host_binary']['path']) != plan['host_binary']['sha256']:
            raise RuntimeError('host executable changed')
        serial = shell('serial', ['getprop', 'ro.serialno']).decode().strip()
        if serial != plan['expected_headset_serial']:
            raise RuntimeError('wrong physical headset')
        receipt['device_serial'] = serial
        shell('fingerprint', ['getprop', 'ro.build.fingerprint'])
        shell('battery-before', ['dumpsys', 'battery'])
        route = json.loads(command('host-route', ['ip', '-j', 'route', 'get', plan['headset_ipv4']]))
        if len(route) != 1 or route[0].get('dev') != plan['expected_host_interface'] or route[0].get('prefsrc') != plan['host_ipv4']:
            raise RuntimeError('unexpected host route')
        device_route = shell('device-route', ['ip', 'route', 'get', plan['host_ipv4']]).decode()
        if 'dev wlan0' not in device_route or plan['headset_ipv4'] not in device_route:
            raise RuntimeError('headset route is not the expected WiFi interface')
        ports = shell('ports-before', ['ss', '-lntu']).decode()
        if port_pattern.search(ports):
            raise RuntimeError('planned receiver port already in use')
        shell('mkdir', ['mkdir', remote])
        for i, file in enumerate(plan['files']):
            if sha(file['local']) != file['sha256'] or file['remote'] not in ('probe', 'grammar-test'):
                raise RuntimeError('device executable changed or unexpected destination')
            dest = remote + '/' + file['remote']
            command('push-' + str(i), adb + ['push', file['local'], dest], timeout=60)
            actual = shell('hash-' + str(i), ['sha256sum', dest]).decode().split()[0]
            if actual != file['sha256']:
                raise RuntimeError('device executable hash differs')
            shell('chmod-' + str(i), ['chmod', '700', dest])
        shell('grammar', ['timeout', '-s', 'KILL', '30', remote + '/grammar-test'], timeout=40)
        grammar = json.loads((out / 'grammar.stdout').read_text())
        if grammar.get('pass') is not True:
            raise RuntimeError('device grammar test did not pass')
        for arm in plan['optional_smoke'] + plan['runs']:
            row = {'name': arm['name'], 'start_utc': datetime.now(timezone.utc).isoformat()}
            receipt['runs'].append(row)
            receipt['status'] = 'running'
            save()
            print('START ' + arm['name'], flush=True)
            receive = ['timeout', '-s', 'KILL', '40'] + [x.replace('{remote}', remote) for x in arm['receiver_argv']]
            rx = endpoint(arm['name'] + '-receiver', adb + ['shell', shlex.join(receive)], 45)
            try:
                # Confirm the exact high port is bound before the sender connects.
                until = time.monotonic() + 3
                for attempt in range(30):
                    if rx[0].poll() is not None or time.monotonic() >= until:
                        raise RuntimeError('receiver did not become ready')
                    listening = shell(arm['name'] + '-ready-' + str(attempt), ['ss', '-lntu'],
                                      timeout=max(.001, until - time.monotonic())).decode()
                    if rx[0].poll() is not None or time.monotonic() >= until:
                        raise RuntimeError('owned receiver exited or readiness deadline expired')
                    state = 'LISTEN' if arm['transport'] == 'tcp' else 'UNCONN'
                    local_address = plan['headset_ipv4'] + ':' + str(arm['receiver_port'])
                    if any(len(fields) >= 5 and fields[0] == arm['transport'] and fields[1] == state
                           and fields[4] == local_address for fields in (line.split() for line in listening.splitlines())):
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError('receiver readiness attempts exhausted')
                tx = endpoint(arm['name'] + '-sender', ['timeout', '-s', 'KILL', '40'] + arm['sender_argv'], 45)
                row['sender_exit'] = wait_endpoint(tx, 45)
            finally:
                row['receiver_exit'] = wait_endpoint(rx, 45)
            if row.get('sender_exit') not in (0, 2) or row['receiver_exit'] not in (0, 2):
                raise RuntimeError('endpoint acquisition failed: ' + arm['name'])
            output = out / (arm['name'] + '-audit.json')
            command(arm['name'] + '-audit', ['python3', str(audit), str(out / (arm['name'] + '-sender.stdout')),
                    str(out / (arm['name'] + '-receiver.stdout')), str(output)])
            row['audit_sha256'] = sha(output)
            row['finish_utc'] = datetime.now(timezone.utc).isoformat()
            save()
            print('AUDITED ' + arm['name'], flush=True)
        receipt['inputs_unchanged'] = sha(plan['host_binary']['path']) == plan['host_binary']['sha256'] \
            and all(sha(f['local']) == f['sha256'] for f in plan['files']) and sha(audit) == receipt['audit_sha256']
        receipt['status'] = 'collection_completed' if receipt['inputs_unchanged'] else 'source_drift'
    except Exception as error:
        receipt['status'] = 'stopped'
        receipt['error'] = str(error)
        raise
    finally:
        errors = []
        for label, argv in [('battery-after', ['dumpsys', 'battery']), ('ports-after', ['ss', '-lntu']),
                            ('cleanup', ['rm', '-rf', remote]), ('cleanup-verified', ['test', '!', '-e', remote])]:
            try:
                shell(label, argv)
            except Exception as error:
                errors.append(label + ': ' + str(error))
        receipt['cleanup_errors'] = errors
        if (out / 'ports-after.stdout').exists():
            receipt['owned_receiver_ports_absent'] = not bool(port_pattern.search((out / 'ports-after.stdout').read_text()))
        receipt['finish_utc'] = datetime.now(timezone.utc).isoformat()
        if errors or receipt.get('owned_receiver_ports_absent') is not True:
            receipt['status'] = 'failed_cleanup'
        save()
        print(json.dumps({'status': receipt['status'], 'receipt': str(out / 'receipt.json')}), flush=True)
        if receipt['status'] in ('failed_cleanup', 'source_drift'):
            raise RuntimeError(receipt['status'])


if __name__ == '__main__':
    main()
