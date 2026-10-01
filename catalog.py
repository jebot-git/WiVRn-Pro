#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Persistent room catalog, independent of WiVRn and live localization."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import tempfile

MAX_BYTES = 32768  # Leave room for both live and archived Quest data in 128 KiB snapshots.


def default_path():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'vrm-room-bridge/catalog.json'


def empty():
    return {'version': 1, 'rooms': {}}


def validate(data):
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('rooms'), dict):
        raise ValueError('Unsupported room catalog')
    if len(json.dumps(data, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Room catalog exceeds v1 limit (32 KiB)')
    for key, room in data['rooms'].items():
        if not key or not isinstance(room, dict) or room.get('alignment') != 'unresolved':
            raise ValueError('Saved rooms must have unresolved live alignment')
        layout = room.get('layout')
        if not isinstance(layout, dict) or layout.get('version') not in (1, 2):
            raise ValueError('Expected companion playspace format v1/v2')
        if not isinstance(layout.get('ground'), dict):
            raise ValueError('Room must contain ground definition')
        for field in ('obstacles', 'spots'):
            if not isinstance(layout.get(field), list):
                raise ValueError('Room must contain obstacles and spots lists')
    return data


def load(path):
    path = Path(path)
    if not path.exists():
        return empty()
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Room catalog exceeds v1 limit')
    return validate(json.loads(raw))


def import_room(path, room_id, source):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path)
        with Path(source).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Room file exceeds v1 limit')
        layout = json.loads(raw)
        data['rooms'][room_id] = {'layout': layout, 'alignment': 'unresolved',
                                  'source': 'companion_import', 'coordinate_space': 'saved_room'}
        validate(data)
        raw = json.dumps(data, separators=(',', ':'), allow_nan=False).encode()
        fd, name = tempfile.mkstemp(prefix='.catalog-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=default_path())
    parser.add_argument('--import-room', required=True, type=Path)
    parser.add_argument('--room-id', default='main')
    args = parser.parse_args()
    import_room(args.path, args.room_id, args.import_room)
    print(f'Saved room {args.room_id!r} in {args.path}; live alignment remains unresolved.')
