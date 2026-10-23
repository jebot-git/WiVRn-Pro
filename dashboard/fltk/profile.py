#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt-free TOML validation/serialization, called asynchronously by the native UI."""
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib


def value(v):
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)): return v.isoformat()
    if isinstance(v, bool): return str(v).lower()
    if isinstance(v, str): return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (float, int)): return repr(v)
    if isinstance(v, list): return '[' + ', '.join(map(value, v)) + ']'
    if isinstance(v, dict): return '{' + ', '.join(json.dumps(k) + ' = ' + value(x) for k, x in v.items() if x is not None) + '}'
    raise ValueError('Unsupported TOML value')


def preserve_dates(value, previous):
    if isinstance(previous, (datetime.datetime, datetime.date, datetime.time)) and value == previous.isoformat(): return previous
    if isinstance(value, dict) and isinstance(previous, dict): return {k: preserve_dates(v, previous.get(k)) for k, v in value.items()}
    if isinstance(value, list) and isinstance(previous, list): return [preserve_dates(v, previous[i] if i < len(previous) else None) for i, v in enumerate(value)]
    return value


def run(request):
    path = Path(request['path'])
    current = path.read_text() if path.exists() else None
    if request['action'] == 'save':
        if current != request['expected']:
            raise ValueError('Tracking profile changed elsewhere. Reload before saving.')
        text = request.get('text')
        if text is None:
            text = '# WiVRn Pro tracking profile\n' + ''.join(json.dumps(k) + ' = ' + value(v) + '\n' for k, v in preserve_dates(request['profile'], tomllib.loads(current or '')).items())
        tomllib.loads(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            # Recheck after validation and IO; matches the dashboard's conflict policy.
            if (path.read_text() if path.exists() else None) != current:
                raise ValueError('Tracking profile changed while saving. Reload before saving.')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        current = text
    return {'ok': True, 'text': current, 'profile': tomllib.loads(current or '')}


if __name__ == '__main__':
    try: response = run(json.load(sys.stdin))
    except (OSError, ValueError, TypeError, KeyError) as error: response = {'ok': False, 'error': str(error)}
    json.dump(response, sys.stdout, default=lambda v: v.isoformat())
