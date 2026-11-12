#!/usr/bin/env python3
"""Exercise the embedded live face controller with simulated serial/HMD data."""
import argparse
import copy
import os
from pathlib import Path
import pty
import socket
import struct
import subprocess
import tempfile
import tomllib

from tracking_worker_smoke import config_file, request, status, wait_for, serial_frame, check_trackers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wivrn-live-face-') as temporary:
        directory = Path(temporary)
        master, slave = pty.openpty()
        profile = config_file(directory, os.ttyname(slave))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as feedback, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as babble, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
            for sock in (feedback, babble, sink): sock.bind(('127.0.0.1', 0))
            feedback_port, babble_port = feedback.getsockname()[1], babble.getsockname()[1]
            profile.write_text(profile.read_text() + f'''[face]
enabled = false
source = "babble"
output = "native"
osc_query = false
osc_port = {feedback_port}
babble_port = {babble_port}
destination = "127.0.0.1:{sink.getsockname()[1]}"
''')
        control = directory / 'control'
        with (directory / 'worker.log').open('w+') as log:
            process = subprocess.Popen([str(args.server.resolve()), '--shora-worker', str(os.getpid()), '--config', str(profile)], stdout=log, stderr=log)
            try:
                wait_for(lambda: status(control))
                def feed():
                    serial_frame(master)
                wait_for(lambda: check_trackers(status(control) or {}), pump=feed)
                with socket.socket(socket.AF_UNIX) as feeder:
                    feeder.connect(str(directory / 'input'))
                    def send(body): feeder.sendall(struct.pack('<I', len(body) + 4) + body)
                    send(b'\x1a\x02\x20\x13')
                    values = (0.0, 1.65, 0.0, 0.0, 0.0, 0.0, 1.0)
                    pose = b''.join(bytes([(field << 3) | 5]) + struct.pack('<f', value) for field, value in enumerate(values, 2))
                    def feed_all():
                        feed(); send(b'\x0a' + bytes([len(pose)]) + pose)
                    wait_for(lambda: status(control)['hmd_pose_received'], pump=feed_all)
                    assert request(control, 'full_reset')['ok']
                    baseline = status(control)
                    assert baseline['calibrated_trackers'] == 2
                    face = copy.deepcopy(baseline['face']['settings'])
                    for enabled, mode in [(True, 'native'), (True, 'unift'), (True, 'auto'), (False, 'auto'), (True, 'native')]:
                        feed_all()
                        face.update(enabled=enabled, output=mode)
                        reply = request(control, {'command': 'set_face_settings', 'face': face})
                        assert reply['ok'], reply
                        current = reply['status']
                        assert current['pid'] == baseline['pid'] == process.pid
                        assert current['settings'] == baseline['settings']
                        assert current['calibrated_trackers'] == 2
                        assert current['hmd_pose_received']
                        assert current['face']['settings']['output'] == mode
                        check_trackers(current)
                    before = profile.read_bytes()
                    bad = dict(face, output='json', translation_sheet=str(directory / 'missing.json'))
                    assert not request(control, {'command': 'set_face_settings', 'face': bad})['ok']
                    assert profile.read_bytes() == before
                    feed_all()
                    current = status(control)
                    assert current['pid'] == baseline['pid'] and current['calibrated_trackers'] == 2
                    assert current['settings'] == baseline['settings']
                    assert tomllib.loads(profile.read_text())['face']['output'] == 'native'
                    # Destroy/recreate the source with another private Babble port.
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as next_port:
                        next_port.bind(('127.0.0.1', 0)); replacement = next_port.getsockname()[1]
                    face['babble_port'] = replacement
                    assert request(control, {'command': 'set_face_settings', 'face': face})['ok']
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as released:
                        released.bind(('127.0.0.1', babble_port))
                    print('PASS embedded live face controller: enable/disable, modes, source replacement, failed mapping, persistence; worker PID, HMD feed, calibrated body trackers and tuning preserved')
            except BaseException:
                log.flush(); log.seek(0); print(log.read())
                raise
            finally:
                if process.poll() is None: process.terminate()
                process.wait(timeout=5)
                os.close(master); os.close(slave)


if __name__ == '__main__': main()
