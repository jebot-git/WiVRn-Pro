#!/usr/bin/env python3
"""Hardware-free Linux smoke tests. Build first; pass --qt to test PySide6 too."""
import argparse
import base64
import fcntl
import json
import math
import os
from pathlib import Path
import pty
import select
import socket
import stat
import struct
import subprocess
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]


def wait_for(check, timeout=8, pump=lambda: None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pump()
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for condition")


def request(path, command):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.sendall(json.dumps(command if isinstance(command, dict) else {"command": command}).encode() + b"\n")
        with client.makefile("rb") as stream:
            return json.loads(stream.readline())


def status(path):
    try:
        return request(path, "status")["status"]
    except (OSError, ValueError):
        return None


def serial_frame(master):
    data = bytearray(30)
    struct.pack_into("<h", data, 6, -18000)
    struct.pack_into("<h", data, 16, 9000)
    struct.pack_into("<h", data, 22, -15588)
    os.write(master, b"r0:0000300000\nX0:" + base64.b64encode(data) + b"\n")
    os.write(master, b'v0:{"battery voltage":3900,"battery remaining":75}\n')
    os.write(master, b"o0:0123456789abcd\n")


def config_file(directory, slave):
    path = directory / "config.toml"
    path.write_text(f'''tracker_port = 0
solarxr_socket = "{directory}/rpc"
feeder_socket = "{directory}/input"
control_socket = "{directory}/control"
[haritorax]
enabled = true
ports = ["{slave}"]
model = "x2"
''')
    return path


def check_trackers(snapshot):
    trackers = {t["name"]: t for t in snapshot.get("trackers", [])}
    if len(trackers) != 2 or not all(t["active"] for t in trackers.values()):
        return False
    assert trackers["leftAnkle"]["position"] == 9
    assert trackers["leftKnee"]["position"] == 7
    assert trackers["leftAnkle"]["mac"] == "7E:E2:E4:79:6A:A8"
    assert trackers["leftAnkle"]["battery_percent"] == 75
    assert snapshot["bone_count"] > 0
    return True


def backend_test(binary):
    with tempfile.TemporaryDirectory(prefix="shora-smoke-") as tmp:
        directory = Path(tmp)
        master, slave = pty.openpty()
        config = config_file(directory, os.ttyname(slave))
        control = directory / "control"
        with (directory / "server.log").open("w+") as log:
            process = subprocess.Popen([str(binary), "--shora-worker", str(os.getpid()), "--config", str(config)], stdout=log, stderr=log)
            try:
                wait_for(lambda: (s := status(control)) and s["ports"] and s["ports"][0]["connected"])
                assert stat.S_IMODE(control.stat().st_mode) == 0o600
                assert not request(control, "yaw_reset")["ok"]
                assert not request(control, "unknown_command")["ok"]
                assert not request(control, "shutdown_trackers")["ok"]
                # Lose the initial identity response, then stream a leg frame
                # before r0 arrives. The server must request identity again;
                # a physical parent-tracker button/reset must not be required.
                while select.select([master], [], [], 0)[0]:
                    os.read(master, 4096)
                serial_frame_bytes = bytearray(30)
                struct.pack_into("<h", serial_frame_bytes, 6, -18000)
                struct.pack_into("<h", serial_frame_bytes, 22, -18000)
                os.write(master, b"X0:" + base64.b64encode(serial_frame_bytes) + b"\n")
                serial_commands = bytearray()
                def identity_requested_again():
                    if select.select([master], [], [], 0)[0]:
                        serial_commands.extend(os.read(master, 4096))
                    return b"r0:" in serial_commands
                wait_for(identity_requested_again)
                serial_frame(master)
                wait_for(lambda: check_trackers(status(control) or {}))
                print("PASS late serial identity recovery discovers knee without parent reset")
                before = status(control)
                assert len(before["bones"]) == before["bone_count"] == 21
                for bone in before["bones"]:
                    assert abs(math.dist(bone["head"], bone["tail"]) - bone["length"]) < 1e-5
                # Exercise the real feeder framing/protobuf, not only the solver.
                with socket.socket(socket.AF_UNIX) as feeder:
                    feeder.connect(str(directory / "input"))
                    def send_feeder(body):
                        feeder.sendall(struct.pack("<I", len(body) + 4) + body)
                    # TrackerAdded: tracker id 0 (default), HMD role 19.
                    send_feeder(b"\x1a\x02\x20\x13")
                    anchored = []
                    anchors = [(1.25, 1.65, -2.5), (-0.75, 1.2, 0.4)]
                    for anchor in anchors:
                        # Position fields 2..8, quaternion x/y/z/w.
                        values = (*anchor, 0.0, 0.0, 0.0, 1.0)
                        body = b"".join(bytes([(field << 3) | 5]) + struct.pack("<f", value)
                                        for field, value in enumerate(values, 2))
                        send_feeder(b"\x0a" + bytes([len(body)]) + body)
                        def at_anchor():
                            snapshot = status(control)
                            if snapshot and snapshot["hmd_pose_received"]:
                                neck = next(b for b in snapshot["bones"] if b["body_part"] == 2)
                                if math.dist(neck["head"], anchor) < 1e-5:
                                    return snapshot
                        anchored.append(wait_for(at_anchor))
                    delta = [b - a for a, b in zip(*anchors)]
                    for first, second in zip(anchored[0]["bones"], anchored[1]["bones"]):
                        for endpoint in ("head", "tail"):
                            expected = [v + d for v, d in zip(first[endpoint], delta)]
                            assert math.dist(second[endpoint], expected) < 1e-5
                wait_for(lambda: not status(control)["hmd_pose_received"])
                print("PASS moving HMD anchors joints; disconnect clears previous session pose")
                settings = dict(before["settings"], height_m=2.0, smoothing=0.6, prediction=0.04,
                                drift_correction=True, drift_amount=0.75)
                assert request(control, {"command": "set_settings", "settings": settings})["ok"]
                first_length = before["bones"][0]["length"]
                wait_for(lambda: (s := status(control)) and abs(s["bones"][0]["length"] / first_length - 2.0 / 1.8) < 1e-4)
                invalid = dict(settings, height_m=1.5, smoothing=2.0)
                rejected = request(control, {"command": "set_settings", "settings": invalid})
                assert not rejected["ok"] and rejected["status"]["settings"]["height_m"] == 2.0
                # Binary garbage, oversized lines and invalid IMU must not stop acquisition.
                os.write(master, b"\xff\n" + b"z" * 9000 + b"\nX0:bad!\nX0:AAAA\n")
                serial_frame(master)
                wait_for(lambda: check_trackers(status(control) or {}))
                for command in ["yaw_reset", "full_reset", "mounting_reset"]:
                    assert request(control, command)["ok"]
                calibrated = status(control)
                assert calibrated["calibrated_trackers"] == 2
                # Rapid resets must not be mistaken for measured IMU drift;
                # mounting also invalidates the previous reference frame.
                assert calibrated["drift_samples"] == 0
                for tracker in calibrated["trackers"]:
                    assert tracker["drift"]["enabled"]
                    assert tracker["drift"]["last_result"] == "waiting_for_baseline"
                assert request(control, "clear_drift")["status"]["drift_samples"] == 0
                print("PASS authoritative skeleton geometry, live tuning, atomic validation, drift controls")
                assert request(control, "pause_tracking")["status"]["paused"]
                assert not request(control, "pause_tracking")["status"]["paused"]
                os.write(master, b"r0:0000301000\n")
                wait_for(lambda: (status(control) or {}).get("last_action") == "YawReset")
                assert request(control, "restart")["ok"]
                wait_for(lambda: (s := status(control)) and s["ports"][0]["connected"])
                serial_frame(master)
                wait_for(lambda: check_trackers(status(control) or {}))
                # The power-off pulse and restore must reach the actual serial
                # worker, while the server and acquisition remain available.
                while select.select([master], [], [], 0)[0]:
                    os.read(master, 4096)
                assert request(control, "shutdown_trackers")["ok"]
                shutdown_bytes = bytearray()
                def shutdown_written():
                    if select.select([master], [], [], 0)[0]:
                        shutdown_bytes.extend(os.read(master, 4096))
                    return b"\no0:0123456789ab2d\n\no0:0123456789abcd\n" in shutdown_bytes
                wait_for(shutdown_written)
                assert process.poll() is None and status(control)["ports"][0]["connected"]
                serial_frame(master)
                wait_for(lambda: check_trackers(status(control) or {}))
                print("PASS tracker power-off writes settings pulse/restore and keeps server alive")
                os.close(master)
                master = None
                wait_for(lambda: (s := status(control)) and not s["ports"][0]["connected"] and not s["trackers"])
                assert request(control, "shutdown")["ok"]
                assert process.wait(timeout=4) == 0
                assert not control.exists()
                print("PASS serial decode → registry → skeleton, battery, buttons, control, reconnect, disconnect, shutdown")
            except Exception:
                log.seek(0)
                print(log.read())
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if master is not None:
                    os.close(master)
                os.close(slave)
        # Refuse to overwrite regular files at the control endpoint.
        control.write_text("keep me")
        result = subprocess.run([str(binary), "--shora-worker", str(os.getpid()), "--config", str(config)], capture_output=True, timeout=4)
        assert result.returncode != 0 and control.read_text() == "keep me"
        print("PASS existing non-socket control path preserved")
        control.unlink()
        with socket.socket(socket.AF_UNIX) as stale:
            stale.bind(str(control))
        process = subprocess.Popen([str(binary), "--shora-worker", str(os.getpid()), "--config", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for(lambda: (s := status(control)) and s["ports"] and s["ports"][0]["error"])
            duplicate = subprocess.run([str(binary), "--shora-worker", str(os.getpid()), "--config", str(config)], capture_output=True, timeout=4)
            assert duplicate.returncode != 0
            assert request(control, "status")["status"]["pid"] == process.pid
            assert request(control, "shutdown")["ok"]
            assert process.wait(timeout=4) == 0
            print("PASS stale socket recovery, missing-device diagnostics, active server protection")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def dashboard_test(binary):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_dashboard import FakeServer
    from wivrn_dashboard import Dashboard
    from shora_qt import STYLE
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='wivrn-tracking-ui-') as tmp:
        directory = Path(tmp)
        master, slave = pty.openpty()
        config = config_file(directory, os.ttyname(slave))
        control = directory / 'control'
        process = subprocess.Popen([str(binary), '--shora-worker', str(os.getpid()), '--config', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        window = Dashboard(FakeServer(), config, control)
        window.show()
        wait = lambda check: wait_for(check, pump=app.processEvents)
        try:
            wait(lambda: window.last_snapshot and window.last_snapshot['ports'] and window.last_snapshot['ports'][0]['connected'])
            serial_frame(master)
            wait(lambda: window.last_snapshot and check_trackers(window.last_snapshot))
            panel = window.tracking
            window.tabs.setCurrentIndex(1)
            wait(lambda: len(panel.preview.projected_bones) == 21)
            panel.smoothing.setValue(0.25)
            panel.apply_button.click()
            wait(lambda: not panel.applying and window.last_snapshot['settings']['smoothing'] == 0.25)
            window.save_tuning()
            import tomllib
            saved = tomllib.loads(config.read_text())
            assert saved['smoothing'] == 0.25
            assert saved['haritorax']['ports'] == [os.ttyname(slave)]
            assert saved['tracker_port'] == 0
            panel.delay.setValue(0); panel.calibrate_button.click()
            wait(lambda: window.last_snapshot['last_action'] == 'FullReset')
            assert window.last_snapshot['calibrated_trackers'] == 2
            window.grab().save('/tmp/wivrn-tracking-preview.png')
            window.close()
            assert process.poll() is None and status(control)
            process.terminate(); process.wait(timeout=5)
            assert process.returncode == 0
            print('PASS unified Qt skeleton, live tuning, full-profile persistence, calibration, attach-only close and SIGTERM shutdown')
            # The written profile must also be accepted by the actual worker on restart.
            process = subprocess.Popen([str(binary), '--shora-worker', str(os.getpid()), '--config', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            restored = wait_for(lambda: status(control))
            assert restored['settings']['smoothing'] == 0.25
            print('PASS integrated worker reloads dashboard-saved tracking profile')
        finally:
            window.close()
            if process.poll() is None:
                process.terminate(); process.wait(timeout=5)
            os.close(master); os.close(slave)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    args = parser.parse_args()
    backend_test(args.server.resolve())
    dashboard_test(args.server.resolve())
