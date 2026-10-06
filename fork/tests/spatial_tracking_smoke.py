#!/usr/bin/env python3
"""Exercise the linked Shora worker's room-reference guard with fake XR/serial data."""
import argparse
import json
import math
import os
from pathlib import Path
import pty
import select
import socket
import struct
import subprocess
import tempfile
import time
from tracking_worker_smoke import config_file, request, serial_frame, status, wait_for


def test(binary):
    with tempfile.TemporaryDirectory(prefix="wivrn-reference-") as tmp:
        directory = Path(tmp)
        master, slave = pty.openpty()
        config = config_file(directory, os.ttyname(slave))
        control = directory / "control"
        bridge = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        bridge.bind(str(directory / "bridge")); bridge.listen(1); bridge.settimeout(8)
        env = dict(os.environ, WIVRN_SPATIAL_SOCKET=str(directory / "bridge"))
        with (directory / "worker.log").open("w+") as log:
            process = subprocess.Popen([str(binary), "--shora-worker", str(os.getpid()), "--config", str(config)],
                                       env=env, stdout=log, stderr=log)
            peer = feeder = None
            try:
                peer, _ = bridge.accept()
                wait_for(lambda: (s := status(control)) and s["ports"] and s["ports"][0]["connected"])
                feeder = socket.socket(socket.AF_UNIX); feeder.connect(str(directory / "input"))
                def send(body): feeder.sendall(struct.pack("<I", len(body)+4)+body)
                send(b"\x1a\x02\x20\x13")
                seq = 0; generation = 1; offset = 0; publish = True; publish_hmd = True
                distributed = False; yaw = 0.0; outlier = False
                last_spatial = last_pose = 0
                def pump():
                    nonlocal seq, last_spatial, last_pose
                    now = time.monotonic()
                    while select.select([master], [], [], 0)[0]: os.read(master,4096)
                    if now-last_pose >= .04:
                        serial_frame(master)
                        if publish_hmd:
                            body = b"".join(bytes([(field << 3) | 5])+struct.pack("<f", value)
                                            for field, value in enumerate((offset,1.6,0,0,math.sin(yaw/2),0,math.cos(yaw/2)),2))
                            send(b"\x0a"+bytes([len(body)])+body)
                        last_pose=now
                    if publish and now-last_spatial >= .2:
                        seq += 1; last_spatial=now
                        rotation = [0,math.sin(yaw/2),0,math.cos(yaw/2)]
                        pose = {"valid":True,"flags":15,"quest_stage":{"position":[offset,1.6,0],"rotation":rotation}}
                        points = [(0,0),(1,0),(0,1)] if distributed else [(0,0),(1,0),(2,0)]
                        markers = [{"uuid":f"{i+1:032x}","persisted":True,"pose":{
                            "valid":True,"flags":15,"quest_stage":{"position":[
                                math.cos(yaw)*x+math.sin(yaw)*z+offset,
                                1.1 if outlier and i == 0 else 1,
                                -math.sin(yaw)*x+math.cos(yaw)*z],"rotation":rotation}}}
                            for i, (x,z) in enumerate(points)]
                        peer.send(json.dumps({"schema":"wivrn.spatial.v1","source_session":"123","sequence":seq,
                            "generation":generation,"sample_monotonic_ns":time.monotonic_ns(),"live":True,
                            "head":pose,"markers":markers}).encode())
                def wait(predicate): return wait_for(lambda: (s := status(control)) and predicate(s) and s, pump=pump)
                assert not request(control,"yaw_reset")["ok"]
                stable = wait(lambda s:s["spatial_reference"]["ready"] and s["hmd_pose_received"])
                assert stable["reference_needs_reset"]
                settings = dict(stable["settings"], drift_correction=True)
                assert request(control,{"command":"set_settings","settings":settings})["ok"]
                pump(); assert request(control,"full_reset")["ok"]
                wait(lambda s:not s["reference_needs_reset"])
                generation += 1
                wait(lambda s:s["reference_needs_reset"] and not s["spatial_reference"]["ready"])
                assert not request(control,"yaw_reset")["ok"]
                wait(lambda s:s["spatial_reference"]["ready"])
                # Recovery alone and a mounting reset cannot unlock automatic learning.
                pump(); assert request(control,"mounting_reset")["ok"]
                assert status(control)["reference_needs_reset"]
                pump(); assert request(control,"yaw_reset")["ok"]
                recovered=wait(lambda s:not s["reference_needs_reset"])
                assert recovered["drift_samples"]==0
                offset=.06
                wait(lambda s:s["reference_needs_reset"] and not s["spatial_reference"]["ready"])
                wait(lambda s:s["spatial_reference"]["ready"])
                pump(); assert request(control,"full_reset")["ok"]
                publish=False
                stale=wait(lambda s:not s["spatial_reference"]["ready"])
                assert stale["reference_needs_reset"]
                assert not request(control,"full_reset")["ok"]
                publish=True
                wait(lambda s:s["spatial_reference"]["ready"])
                pump(); assert request(control,"full_reset")["ok"]
                publish_hmd=False
                wait(lambda s:not s["hmd_pose_received"] and s["reference_needs_reset"])
                assert not request(control,"yaw_reset")["ok"]
                publish_hmd=True
                wait(lambda s:s["hmd_pose_received"])
                pump(); assert request(control,"full_reset")["ok"]
                # A spatially distributed set supports automatic same-session recovery.
                distributed=True
                wait(lambda s:s["reference_needs_reset"])
                wait(lambda s:s["spatial_reference"]["ready"])
                pump(); assert request(control,"full_reset")["ok"]
                for next_yaw in (0.3, -0.4):
                    yaw=next_yaw; generation+=1
                    wait(lambda s:s["reference_needs_reset"] and not s["spatial_reference"]["ready"])
                    recovered=wait(lambda s:s["spatial_reference"]["ready"] and not s["reference_needs_reset"])
                    assert recovered["drift_samples"]==0
                    def aligned(s):
                        bones=[b for b in s["bones"] if b["tracked"]]
                        return bones and all(abs(sum(a*b for a,b in zip(bone["rotation"],
                            [0,math.sin(yaw/2),0,math.cos(yaw/2)]))) > .9999 for bone in bones)
                    wait(aligned)
                    revision=recovered["spatial_reference"]["revision"]
                    sequence=seq
                    stable=wait(lambda s:seq >= sequence+5 and aligned(s))
                    assert stable["spatial_reference"]["revision"]==revision
                # Even one disagreeing anchor restores the deliberate-reset gate.
                outlier=True
                wait(lambda s:s["reference_needs_reset"])
                stable=wait(lambda s:s["spatial_reference"]["ready"])
                assert stable["reference_needs_reset"]
                peer.close(); peer=None
                # Stop publishing before the next pump after closing the bridge peer.
                publish=False
                lost=wait(lambda s:s["spatial_reference"]["state"]=="spatial_bridge_disconnected")
                assert lost["reference_needs_reset"]
                assert not request(control,"yaw_reset")["ok"]
                print("PASS integrated spatial reference: reset gating, automatic yaw rebase, outlier rejection, expiry, feeder loss and disconnect")
            except BaseException:
                log.flush(); log.seek(0); print(log.read()); raise
            finally:
                process.terminate(); process.wait(timeout=8)
                if feeder: feeder.close()
                if peer: peer.close()
                bridge.close(); os.close(master); os.close(slave)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--server",type=Path,required=True)
    test(parser.parse_args().server.resolve())
