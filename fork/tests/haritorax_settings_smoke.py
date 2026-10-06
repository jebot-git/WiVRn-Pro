#!/usr/bin/env python3
"""Exercise confirmed and unconfirmed sensor writes against a simulated GX dongle."""
import argparse
import base64
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import threading
import time
from tracking_worker_smoke import config_file, request, status, wait_for


def test(binary):
    with tempfile.TemporaryDirectory(prefix="wivrn-sensors-") as tmp:
        directory = Path(tmp)
        master, slave = pty.openpty()
        config = config_file(directory, os.ttyname(slave))
        control = directory / "control"
        stop = threading.Event()
        writes = []
        acknowledge = threading.Event(); acknowledge.set()
        def dongle():
            value = "abcd0000103001"
            incoming = b""
            data = bytearray(30)
            struct.pack_into("<h", data, 6, -18000)
            while not stop.is_set():
                if select.select([master], [], [], .02)[0]:
                    incoming += os.read(master, 4096)
                    while b"\n" in incoming:
                        line, incoming = incoming.split(b"\n", 1)
                        if line.startswith(b"o0:") and len(line) == 17:
                            writes.append(line.decode())
                            if acknowledge.is_set():
                                value = line[3:].decode()
                os.write(master, b"r0:0000300000\nX0:" + base64.b64encode(data) + b"\n")
                os.write(master, f"o0:{value}\n".encode())
        with (directory / "worker.log").open("w+") as log:
            process = subprocess.Popen([str(binary), "--shora-worker", str(os.getpid()), "--config", str(config)], stdout=log, stderr=log)
            thread = None
            try:
                wait_for(lambda: (s := status(control)) and s["ports"] and s["ports"][0]["connected"])
                thread = threading.Thread(target=dongle); thread.start()
                ready = wait_for(lambda: (s := status(control)) and s["ports"][0]["trackers"] and s)
                tracker = ready["ports"][0]["trackers"][0]
                hardware = dict(tracker["settings"], sensor_mode=2, fps=100, magnetometer=False)
                command = {"command":"set_haritora_settings", "tracker_mac":tracker["mac"], "hardware":hardware}
                response = request(control, command)
                assert response["ok"], response["error"]
                assert response["status"]["ports"][0]["trackers"][0]["settings"] == hardware
                assert writes == ["o0:abcd0100103001"], writes
                assert len(response["status"]["ports"][0]["trackers"]) == 1  # no duplicate extension write
                command["hardware"] = dict(hardware, fps=90)
                assert not request(control, command)["ok"]
                assert process.poll() is None and len(writes) == 1
                acknowledge.clear()
                command["hardware"] = dict(hardware, fps=50)
                result = []
                operation = threading.Thread(target=lambda: result.append(request(control, command)))
                operation.start()
                time.sleep(.3)
                # Sensor acknowledgement never stalls tracking/status commands.
                before = time.monotonic()
                assert request(control, "pause_tracking")["ok"]
                assert time.monotonic()-before < .5
                assert request(control, "pause_tracking")["ok"]
                operation.join(3)
                assert result and not result[0]["ok"]
                assert result[0]["status"]["ports"][0]["trackers"][0]["settings"]["fps"] == 100
                assert len(writes) == 2
                time.sleep(.2); assert len(writes) == 2  # no retry or replay
                assert request(control, "shutdown")["ok"]
                process.wait(timeout=5)
                assert process.returncode == 0
            finally:
                stop.set()
                if thread: thread.join(2)
                if process.poll() is None: process.terminate(); process.wait(timeout=5)
                os.close(master); os.close(slave)
                if process.returncode: log.seek(0); print(log.read())
    print("HaritoraX settings smoke passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("binary", type=Path)
    test(parser.parse_args().binary.resolve())
