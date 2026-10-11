#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated Motoc mode only: no Lighthouse driver, GPU, headset or live service."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('--server', required=True)
args = parser.parse_args()
server = str(Path(args.server).resolve())

def request(path, command):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.sendall(json.dumps(command if isinstance(command, dict) else {'command': command}).encode() + b'\n')
        with client.makefile('rb') as stream:
            return json.loads(stream.readline())

def wait_for(check):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = check()
        if result: return result
        time.sleep(0.02)
    raise AssertionError('Simulated worker timed out')

with tempfile.TemporaryDirectory(prefix='wivrn-motoc-test-') as directory:
    root = Path(directory)
    # Occupying the configured UDP port proves Motoc mode does not acquire SlimeVR.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
        profile = root/'shora.toml'
        profile.write_text(f'''tracker_port = {port}
[face]
enabled = false
[motoc]
enabled = true
source = "WiVRn HMD"
target = "LHR-test"
mode = "once"
keep_alive = false
''')
        fake = root/'motoc'
        fake.write_text('''#!/usr/bin/python3
import json, os, sys, time
with open(os.environ['MOTOC_TEST_ARGS'], 'w') as output:
    json.dump({'args':sys.argv[1:], 'pid':os.getpid()}, output)
print('WIVRN_MOTOC_READY', flush=True)
time.sleep(60)
''')
        fake.chmod(0o700)
        control = root/'control'
        output = root/'arguments.json'
        env = {**os.environ, 'WIVRN_TRACKING_MODE':'motoc', 'WIVRN_MOTOC_EXECUTABLE':str(fake),
               'MOTOC_TEST_ARGS':str(output)}
        env.pop('WIVRN_SPATIAL_SOCKET', None)
        with (root/'log').open('w') as log:
            worker = subprocess.Popen([server, '--shora-worker', str(os.getpid()), '--config', str(profile),
                '--ui', 'headless', '--control-socket', str(control), '--solarxr-socket', str(root/'solarxr'),
                '--feeder-socket', str(root/'feeder')], env=env, stdout=log, stderr=log)
            try:
                wait_for(control.exists)
                wait_for(lambda: request(control,'status')['status']['tracking_mode'] == 'motoc')
                assert not (root/'solarxr').exists() and not (root/'feeder').exists()
                rejected = request(control,'full_reset')
                assert not rejected['ok'] and 'SlimeVR is disabled' in rejected['error']
                assert request(control,'motoc_calibrate')['ok']
                wait_for(output.exists)
                launched = json.loads(output.read_text())
                assert launched['args'] == ['calibrate','--src','WiVRn HMD','--dst','LHR-test','--profile','wivrn-pro-once']
                assert request(control,'motoc_stop')['ok']
                assert not request(control,'status')['status']['motoc']['running']
                try: os.kill(launched['pid'], 0)
                except ProcessLookupError: pass
                else: raise AssertionError('Motoc child survived Stop')
            finally:
                worker.terminate()
                try: worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill(); worker.wait(); raise
print('PASS exclusive Motoc mode, no SlimeVR sockets/UDP, wrong-mode rejection, calibration arguments and child cleanup')
