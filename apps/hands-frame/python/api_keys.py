# SPDX-License-Identifier: GPL-3.0-or-later
from settings import atomic_json, private_json

def save(path,value):
    value=value.strip()
    if not value or len(value)>512 or any(c.isspace() or ord(c)<33 for c in value):raise ValueError('Enter a valid API key')
    atomic_json(path,{'key':value})

def read(path,name):
    try:
        value=private_json(path,4096)['key']
        if not isinstance(value,str) or not value or len(value)>512 or any(c.isspace() or ord(c)<33 for c in value):raise ValueError()
        return value
    except (OSError,ValueError,KeyError,TypeError):raise ValueError('Add your '+name+' key in Hands Frame settings') from None

def stamp(path):
    try:return path.stat().st_mtime_ns
    except OSError:return 0
