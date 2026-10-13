#!/usr/bin/env python3
"""Local audio A/B switcher and guided recorder. No action runs on import."""
import argparse
import array
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave

PACKAGE = 'org.meumeu.wivrn.spatial'
SERVICE = 'wivrn-pro.service'
SERVER = Path('/home/blux/.local/opt/wivrn-pro/bin/wivrn-pro-server')
KIT = Path(__file__).resolve().parent
if not (KIT / 'manifest.json').exists():
    KIT = KIT.parents[1].parent / 'artifacts/audio-ab-20261007'


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, **kwargs):
    return subprocess.check_output(list(map(str, args)), text=True, stderr=subprocess.STDOUT, timeout=90, **kwargs).strip()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def manifest():
    data = json.loads((KIT / 'manifest.json').read_text())
    for variant, item in data['artifacts'].items():
        for kind in ('apk', 'server'):
            path = KIT / item[kind]
            if sha(path) != item[kind + '_sha256']:
                raise RuntimeError(f'{variant} {kind} checksum mismatch')
    return data


def device(serial=None):
    if not serial:
        ready = [line.split()[0] for line in run(['adb', 'devices']).splitlines()[1:] if line.endswith('\tdevice')]
        if len(ready) != 1:
            raise RuntimeError('Connect exactly one authorized headset, or use --serial.')
        serial = ready[0]
    return ['adb', '-s', serial]


def package_state(adb):
    text = run(adb + ['shell', 'dumpsys', 'package', PACKAGE])
    code = re.search(r'versionCode=(\d+)', text)
    name = re.search(r'versionName=(\S+)', text)
    if not code or not name:
        raise RuntimeError('WiVRn Pro is not installed on this headset.')
    path = run(adb + ['shell', 'pm', 'path', PACKAGE]).removeprefix('package:')
    if '\n' in path:
        raise RuntimeError('Split APK installation is not supported by this kit.')
    digest = run(adb + ['shell', 'sha256sum', shlex.quote(path)]).split()[0]
    return {'version_code': int(code[1]), 'version_name': name[1], 'path': path, 'apk_sha256': digest}


def graph():
    data = json.loads(run(['pw-dump']))
    nodes = {r['id']: r.get('info', {}) for r in data if r.get('type') == 'PipeWire:Interface:Node'}
    mic = [i for i, info in nodes.items() if info.get('props', {}).get('node.name') == 'wivrn.source']
    readers = {}
    for item in data:
        info = item.get('info', {})
        if item.get('type') == 'PipeWire:Interface:Link' and info.get('output-node-id') in mic:
            node_id = info['input-node-id']
            props = nodes.get(node_id, {}).get('props', {})
            readers[node_id] = {'id': node_id, 'name': props.get('application.name', props.get('node.name')), 'node_name': props.get('node.name'), 'link_state': info.get('state')}
    return {'utc': utc(), 'source_present': bool(mic), 'source_state': [nodes[i].get('state') for i in mic], 'readers': list(readers.values())}


def active():
    return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', SERVICE]).returncode == 0


def installed(data, adb):
    package = package_state(adb)
    server = sha(SERVER)
    matched = [name for name, item in data['artifacts'].items() if item['apk_sha256'] == package['apk_sha256'] and item['server_sha256'] == server]
    return {'variant': matched[0] if matched else 'unmatched pair', 'package': package, 'server_sha256': server, 'service_active': active(), 'pipewire': graph()}


def replace_server(payload):
    temporary = SERVER.with_name('.wivrn-pro-server-audio-ab')
    shutil.copy2(payload, temporary)
    if sha(temporary) != sha(payload):
        raise RuntimeError('Staged server checksum mismatch')
    os.replace(temporary, SERVER)


def switch(data, adb, variant):
    before = package_state(adb)
    if before['version_code'] != data['version_code']:
        raise RuntimeError('Installed version changed since this kit was prepared. Refusing to downgrade or uninstall app data.')
    if variant not in data['artifacts']:
        raise RuntimeError(f'No prepared {variant} artifact')
    item = data['artifacts'][variant]
    txn = KIT / 'transactions' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    txn.mkdir(parents=True, mode=0o700)
    run(adb + ['pull', before['path'], str(txn / 'previous.apk')])
    assert sha(txn / 'previous.apk') == before['apk_sha256']
    shutil.copy2(SERVER, txn / 'previous-server')
    was_active = active()
    result = {'started_at': utc(), 'variant': variant, 'previous': before, 'previous_service_active': was_active}
    save(txn / 'receipt.json', result)
    print(f'Switching client and server to {variant}; the VR connection will restart.', flush=True)
    try:
        run(['systemctl', '--user', 'stop', SERVICE])
        run(adb + ['install', '-r', str(KIT / item['apk'])])
        replace_server(KIT / item['server'])
        if was_active:
            run(['systemctl', '--user', 'start', SERVICE])
        run(adb + ['shell', 'am', 'start', '-n', PACKAGE + '/org.meumeu.wivrn.MainActivity'])
        verified = package_state(adb)
        if verified['apk_sha256'] != item['apk_sha256'] or sha(SERVER) != item['server_sha256']:
            raise RuntimeError('Installed pair failed verification')
        result.update(result='installed', finished_at=utc())
    except BaseException as error:
        errors = []
        for operation in [lambda: run(['systemctl', '--user', 'stop', SERVICE]),
                          lambda: run(adb + ['install', '-r', str(txn / 'previous.apk')]),
                          lambda: replace_server(txn / 'previous-server'),
                          lambda: run(['systemctl', '--user', 'start', SERVICE]) if was_active else None,
                          lambda: run(adb + ['shell', 'am', 'start', '-n', PACKAGE + '/org.meumeu.wivrn.MainActivity'])]:
            try:
                operation()
            except BaseException as rollback_error:
                errors.append(str(rollback_error))
        result.update(result='rollback attempted', error=str(error), rollback_errors=errors, finished_at=utc())
        raise
    finally:
        save(txn / 'receipt.json', result)
    print('Verified matching pair. App data retained. Receipt:', txn / 'receipt.json')


def stop_child(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def wave_stats(path):
    with wave.open(str(path), 'rb') as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise ValueError('Expected mono signed 16-bit PCM')
        rate = audio.getframerate()
        seconds = []
        count = 0
        while raw := audio.readframes(rate):
            values = array.array('h', raw)
            if sys.byteorder != 'little':
                values.byteswap()
            count += len(values)
            rms = math.sqrt(sum(x * x for x in values) / len(values))
            seconds.append({'second': len(seconds), 'samples': len(values), 'rms_dbfs': 20 * math.log10(rms / 32768) if rms else None,
                            'peak': max(abs(x) for x in values), 'zero_fraction': sum(x == 0 for x in values) / len(values)})
        return {'file': path.name, 'rate': rate, 'sample_count': count, 'audio_seconds': count / rate, 'per_second': seconds,
                'interpretation': 'Silence alone does not establish a fault. Compare marked speech intervals and listen to the recording. WAV time can differ from wall time during a stalled source.'}


def record(data, adb, label, cycles):
    state = installed(data, adb)
    if state['variant'] not in ('A', 'B'):
        raise RuntimeError('Switch to a matched A or B pair first.')
    if not state['service_active'] or not state['pipewire']['source_present']:
        raise RuntimeError('Connect the headset to WiVRn before starting a run.')
    if state['pipewire']['readers']:
        raise RuntimeError('Close other microphone readers first: ' + ', '.join(str(x['name']) for x in state['pipewire']['readers']))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', label):
        raise ValueError('Use a simple label such as A1 or B2.')
    if re.fullmatch(r'[AB]\d+', label) and label[0] != state['variant']:
        raise ValueError('Run label does not match the installed variant.')
    folder = KIT / 'runs' / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + label)
    folder.mkdir(parents=True, mode=0o700)
    start = time.monotonic()
    events = []
    children = []
    handles = []
    polling_stop = threading.Event()
    graph_issues = []

    def mark(kind, **extra):
        events.append({'event': kind, 'utc': utc(), 'elapsed_seconds': time.monotonic() - start, **extra})
        save(folder / 'events.json', events)

    def spawn(command, name):
        handle = (folder / name).open('w')
        handles.append(handle)
        process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
        children.append(process)
        return process

    def snapshot():
        with (folder / 'pipewire.jsonl').open('w') as stream:
            while not polling_stop.is_set():
                try:
                    value = graph()
                    value['elapsed_seconds'] = time.monotonic() - start
                    others = [r for r in value['readers'] if r['node_name'] != 'wivrn-audio-ab']
                    if others:
                        graph_issues.append({'utc': value['utc'], 'competing_readers': others})
                except Exception as error:
                    value = {'utc': utc(), 'error': str(error)}
                    graph_issues.append(value)
                stream.write(json.dumps(value) + '\n')
                stream.flush()
                polling_stop.wait(1)

    def speech(name):
        mark(name + '_speech_start')
        print('Speak for 10 seconds: “Microphone test, one two three four five.”', flush=True)
        time.sleep(10)
        mark(name + '_speech_end')
        answer = input('Any observed problem? Press Enter if none; listen to recorded speech after the run: ').strip()
        mark(name + '_observation', note=answer)

    def recorder(name):
        command = ['pw-record', '--target', 'wivrn.source', '--rate', '48000', '--channels', '1', '--format', 's16',
                   '--properties', 'node.name=wivrn-audio-ab application.name=WiVRn-Audio-AB node.dont-reconnect=true node.dont-fallback=true', str(folder / name)]
        process = spawn(command, name + '.log')
        for attempt in range(10):
            time.sleep(0.5)
            if process.poll() is not None:
                raise RuntimeError('Recorder exited; inspect ' + str(folder / (name + '.log')))
            current = graph()
            if any(r['node_name'] == 'wivrn-audio-ab' and r['link_state'] == 'active' for r in current['readers']):
                break
        else:
            raise RuntimeError('Recorder did not link to the WiVRn microphone.')
        mark('recording_started', file=name)
        return process

    print('Use the same headset position and room for all runs. Microphone ON, Unprocessed OFF, fixed 90 Hz.')
    print('Close voice-call apps. Do not use Restart microphone, toggle mute, or reconnect during a run.')
    print('This command records your microphone locally until the run ends. Ctrl+C stops and saves it.')
    settings = input('Confirm settings and a freshly started client, then press Enter: ')
    state['settings_confirmation'] = settings or 'confirmed'
    state['headset_clock_start'] = run(adb + ['shell', 'date', '+%Y-%m-%dT%H:%M:%S%z'])
    save(folder / 'start.json', state)
    poller = threading.Thread(target=snapshot, daemon=True)
    capture = None
    try:
        spawn(adb + ['logcat', '-v', 'threadtime', '-T', '1', 'WiVRn:V', 'AAudio:V', 'AAudioStream:V', 'AudioRecord:V', 'AudioFlinger:I', 'AudioPolicyManager:I', '*:S'], 'headset.log')
        spawn(['journalctl', '--user', '-u', SERVICE, '-f', '-n', '0', '-o', 'short-iso'], 'server.log')
        poller.start()
        mark('run_start', variant=state['variant'], label=label)
        capture = recorder('continuous.wav')
        input('Wear the headset. Press Enter when ready for baseline speech: ')
        speech('baseline')
        input('Set the headset down WITHOUT pressing power, then press Enter: ')
        mark('off_head_start')
        time.sleep(15)
        input('Put the headset back on. Press Enter as soon as the view returns: ')
        mark('off_head_return')
        speech('off_head_return')
        for cycle in range(1, cycles + 1):
            input(f'Sleep cycle {cycle}/{cycles}: press headset power briefly to sleep, set it down, then press Enter: ')
            mark('sleep_start', cycle=cycle)
            print('Leave it asleep for 60 seconds.', flush=True)
            time.sleep(60)
            input('Wake and wear the headset. Press Enter as soon as the view returns: ')
            mark('wake', cycle=cycle)
            speech(f'wake_{cycle}')
            if capture.poll() is not None:
                raise RuntimeError('Recorder stopped unexpectedly; run is incomplete.')
        stop_child(capture)
        mark('recording_stopped', file='continuous.wav')
        time.sleep(1)
        gap_state = graph()
        if gap_state['readers']:
            raise RuntimeError('Another microphone reader appeared; the reader-gap test is invalid.')
        mark('reader_gap_start')
        print('Keep the headset awake and on your head. No PC mic reader for 30 seconds.', flush=True)
        time.sleep(30)
        if graph()['readers']:
            raise RuntimeError('A competing reader appeared during the gap.')
        mark('reader_gap_end')
        capture = recorder('after-reader-gap.wav')
        speech('reader_return')
        mark('run_complete')
    except BaseException as error:
        mark('run_interrupted', reason=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        for process in reversed(children):
            stop_child(process)
        polling_stop.set()
        if poller.is_alive():
            poller.join(timeout=5)
        for handle in handles:
            handle.close()
        stats = []
        for path in folder.glob('*.wav'):
            try:
                stats.append(wave_stats(path))
            except Exception as error:
                stats.append({'file': path.name, 'error': str(error)})
        save(folder / 'audio-summary.json', stats)
        save(folder / 'validity.json', {
            'completed': any(e['event'] == 'run_complete' for e in events),
            'graph_issues': graph_issues,
            'requires_review': 'Verify actual sleep/wake transitions in headset.log and listen to marked speech. Competing readers invalidate the demand-lifecycle comparison. PipeWire snapshots are sampled once per second.',
        })
        print('Saved recordings, observations and logs:', folder)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', help='ADB device serial; inferred when exactly one is connected')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Read-only artifact, installed-pair and microphone-reader check')
    change = sub.add_parser('switch', help='Install the matching A/B pair or restore the original pair')
    change.add_argument('variant', choices=['A', 'B', 'restore'])
    capture = sub.add_parser('run', help='Guide a local microphone recording and sleep/wake test')
    capture.add_argument('label', help='Run label, e.g. A1, B1, B2, A2')
    capture.add_argument('--cycles', type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    os.umask(0o077)
    data = manifest()
    adb = device(args.serial)
    with (KIT / '.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another A/B switch or recording is running.')
        if args.command == 'status':
            print(json.dumps(installed(data, adb), indent=2))
        elif args.command == 'switch':
            switch(data, adb, args.variant)
        else:
            record(data, adb, args.label, args.cycles)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f'Stopped: {error}', file=sys.stderr)
        sys.exit(1)
