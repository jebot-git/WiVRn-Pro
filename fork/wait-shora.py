#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded backend-listener readiness check used only at service startup."""
import argparse
import socket
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('paths', nargs='+')
parser.add_argument('--timeout', type=float, default=15)
args = parser.parse_args()
pending = set(args.paths)
deadline = time.monotonic() + args.timeout
while pending and time.monotonic() < deadline:
    for path in tuple(pending):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.2)
                connection.connect(path)
            pending.remove(path)
        except OSError:
            pass
    if pending:
        time.sleep(0.05)
if pending:
    raise SystemExit('Shora listeners did not become ready: ' + ', '.join(sorted(pending)))
