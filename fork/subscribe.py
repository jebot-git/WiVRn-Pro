#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the native WiVRn spatial API; no XR runtime or engine dependency."""
import argparse
import json
import os
from pathlib import Path
import socket
import time


def checked(record):
    """Recheck freshness at consumption time, including packets queued in the socket."""
    if record.get("schema") != "wivrn.spatial.v1":
        raise ValueError("Unsupported spatial schema")
    sampled = record.get("sample_monotonic_ns", 0)
    age = time.monotonic_ns() - sampled
    if record.get("live") and not -100_000_000 <= age <= min(record.get("ttl_ns", 0), 750_000_000):
        record.update(live=False, status="expired_at_reader", alignment=None)
        empty = dict(valid=False, flags=0, quest_stage=None, pc_stage=None)
        record["head"] = empty.copy()
        for item in record.get("markers", []) + record.get("scene", {}).get("entities", []):
            item["pose"] = empty.copy()
    return record


def main():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=runtime / "wivrn-pro/bridge.sock")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = parser.parse_args()
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
                connection.settimeout(2)
                connection.connect(str(args.socket))
                while True:
                    data, _, flags, _ = connection.recvmsg(128 * 1024)
                    if not data:
                        raise ConnectionError("WiVRn spatial session ended")
                    if flags & socket.MSG_TRUNC:
                        raise ValueError("Oversized spatial record")
                    print(json.dumps(checked(json.loads(data)), separators=(",", ":")), flush=True)
                    if args.once:
                        return
        except (OSError, ConnectionError, ValueError) as error:
            if args.once:
                parser.exit(1, f"{error}\n")
            print(json.dumps(dict(schema="wivrn.spatial.v1", live=False, status="disconnected")), flush=True)
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
