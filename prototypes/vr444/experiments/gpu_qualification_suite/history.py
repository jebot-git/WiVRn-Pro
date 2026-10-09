#!/usr/bin/env python3
"""Verify pinned GPU research receipts against historical Git blobs.

This verifies archive integrity. It never reruns a benchmark or treats an old
receipt as qualification of the current worktree, driver, or headset.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

SOURCE_SUFFIXES = {'.cpp', '.c', '.hpp', '.h', '.py', '.sh', '.comp'}
HEX = re.compile(r'^[0-9a-f]{64}$')
COMMIT = re.compile(r'^[0-9a-f]{40}$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    if not isinstance(value, str) or not value or '\0' in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and '..' not in path.parts and str(path) == value


def bindings(value):
    """Read the two historical receipt forms without interpreting their claims."""
    found = {}

    def add(path, sha, strict=False):
        valid = isinstance(path, str) and isinstance(sha, str) and HEX.fullmatch(sha)
        if strict and not valid:
            raise ValueError('malformed receipt digest')
        if valid:
            if path in found and found[path] != sha:
                raise ValueError(f'conflicting receipt digests for {path}')
            found[path] = sha

    def walk(item):
        if isinstance(item, dict):
            if 'path' in item and 'sha256' in item:
                add(item['path'], item['sha256'], True)
            for key, child in item.items():
                if '/' in key:
                    add(key, child, PurePosixPath(key).suffix in SOURCE_SUFFIXES)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def verify_receipt(data, read_blob, required_sources=1):
    if type(required_sources) is not int or required_sources < 1:
        raise ValueError('positive exact source-binding minimum required')
    receipt = json.loads(data)
    verified, external = {}, {}
    for path, expected in bindings(receipt).items():
        if not safe_path(path):
            if PurePosixPath(path).suffix in SOURCE_SUFFIXES:
                raise ValueError(f'invalid historical source path: {path}')
            external[path] = expected
            continue
        content = read_blob(path)
        if content is None:
            if PurePosixPath(path).suffix in SOURCE_SUFFIXES:
                raise ValueError(f'missing historical source: {path}')
            external[path] = expected
            continue
        if digest(content) != expected:
            raise ValueError(f'historical hash mismatch: {path}')
        verified[path] = expected
    sources = {path: sha for path, sha in verified.items()
               if PurePosixPath(path).suffix in SOURCE_SUFFIXES}
    if len(sources) < required_sources:
        raise ValueError('receipt has insufficient historical source bindings')
    return dict(receipt_sha256=digest(data), verified_tracked_bindings=verified,
                verified_source_count=len(sources),
                external_artifact_hashes_not_reverified=external)


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], check=check,
                          capture_output=True)


def verify_entry(root, entry):
    commit = entry['commit']
    if not COMMIT.fullmatch(commit):
        raise ValueError('a complete immutable commit ID is required')
    if git(root, 'cat-file', '-t', commit).stdout.strip() != b'commit':
        raise ValueError('pinned object is not a commit')
    names = set(git(root, 'ls-tree', '-r', '--name-only', '-z', commit).stdout.decode().split('\0'))

    def read(path):
        if not safe_path(path):
            raise ValueError('unsafe Git path')
        if path not in names:
            return None
        return git(root, 'show', f'{commit}:{path}').stdout

    reports = []
    for item in entry['receipts']:
        path = item['path']
        data = read(path)
        if data is None or digest(data) != item['sha256']:
            raise ValueError(f'missing or changed pinned receipt: {path}')
        report = verify_receipt(data, read, item['minimum_source_bindings'])
        reports.append(dict(path=path, **report))
    if not reports:
        raise ValueError('empty historical evidence entry')
    return dict(id=entry['id'], branch=entry['branch'], commit=commit,
                archive_integrity_pass=True, receipts=reports)


def verify_catalog(root, catalog):
    entries = catalog['approaches']
    if catalog.get('schema') != 1 or not entries:
        raise ValueError('missing research catalog')
    names = [item['id'] for item in entries]
    if len(set(names)) != len(names):
        raise ValueError('duplicate research approach')
    return dict(schema=1, archive_integrity_pass=True,
                fresh_execution=False, headset_used=False, promotion_eligible=False,
                approaches=[verify_entry(root, item) for item in entries])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path(__file__).with_name('catalog.json'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    print(json.dumps(verify_catalog(args.root, json.loads(args.catalog.read_text())), indent=2))


if __name__ == '__main__':
    main()
