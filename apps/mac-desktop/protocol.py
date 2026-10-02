# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded framing for the initial SSH-carried Mac desktop relay."""
import json
import re
import struct

HEADER = struct.Struct('!4sBBI')
MAX_PAYLOAD = 4 * 1024 * 1024
INFO, JPEG, STATUS = 1, 2, 3
WIDTH, HEIGHT = 1920, 1080

class FrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        # QProcess is drained in bounded chunks, limiting pending memory to
        # one maximum-size frame plus this chunk.
        if len(data) > 65536:
            raise ValueError('Desktop relay read exceeds chunk limit')
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= HEADER.size:
            magic, version, kind, size = HEADER.unpack_from(self.buffer)
            if magic != b'WVRD' or version != 1 or kind not in (INFO, JPEG, STATUS):
                raise ValueError('Invalid desktop relay header')
            if not 0 < size <= MAX_PAYLOAD:
                raise ValueError('Desktop relay frame exceeds limit')
            if len(self.buffer) < HEADER.size + size:
                break
            payload = bytes(self.buffer[HEADER.size:HEADER.size + size])
            del self.buffer[:HEADER.size + size]
            frames.append((kind, payload))
        return frames


def ssh_arguments(host, user):
    # Do not let host/user become SSH options or shell syntax.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,252}', host):
        raise ValueError('Invalid SSH host')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9._-]{0,63}', user):
        raise ValueError('Use the macOS account short name')
    # The token never appears on the local terminal or in process arguments.
    command = '''token_path="$HOME/Library/Application Support/Mac Desktop Relay/relay.token"
[ -r "$token_path" ] || token_path="$HOME/Library/Application Support/WiVRn-Pro/relay.token"
token=$(cat "$token_path") || exit 1
{ printf '{"op":"auth","token":"%s"}\\n' "$token"; cat; } | /usr/bin/nc 127.0.0.1 9758'''
    return ['-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ServerAliveInterval=3',
            '-o', 'ServerAliveCountMax=2', '-l', user, host, command]


def command(sequence, op, **values):
    return (json.dumps(dict(seq=sequence, op=op, **values), separators=(',', ':'), allow_nan=False) + '\n').encode()
