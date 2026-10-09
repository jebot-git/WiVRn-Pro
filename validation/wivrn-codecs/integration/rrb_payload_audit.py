#!/usr/bin/env python3
"""Bounded offline RRB capture audit using an explicitly hash-pinned scalar oracle.

Reads captured files and executes only a local reference binary. No ADB/network.
The root build receipt must separately bind that binary to rrb_reference.cpp and
its codec dependencies. This proves decoded payload/reference/source agreement,
not acquisition authenticity, rendered image equality or original app pixels.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import zlib

MAX_PAYLOAD = 16 * 1024 * 1024
MAX_PIXELS = 4194304
CAP = 262144


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path, limit):
    need(path.is_file() and not path.is_symlink(), 'regular capture input required: '+str(path))
    need(0 < path.stat().st_size <= limit, 'capture byte bound: '+str(path))
    raw = path.read_bytes()
    need(0 < len(raw) <= limit, 'capture changed size: '+str(path))
    return raw


def envelope(raw, body, frame, eye):
    need(len(raw) >= 48, 'short WCT1 envelope')
    magic, kind, width, height, tag, n, index, warp, crc, flags = struct.unpack('<6I2Q2I', raw[:48])
    need(magic == 0x31544357 and kind == 4 and flags == 0, 'wrong WCT1 RRB kind/flags')
    need(tag == eye and index == frame, 'capture filename/envelope identity')
    need(2 <= width <= 4096 and 2 <= height <= 4096 and width * height <= MAX_PIXELS, 'envelope shape')
    need(0 < n <= MAX_PAYLOAD and len(raw) == 48+n and raw[48:] == body, 'payload/envelope bytes')
    need(zlib.crc32(body) == crc, 'outer CRC')
    return dict(width=width, height=height, warp=warp, bytes=len(raw))


def audit(directory, reference_binary, reference_sha256, source):
    directory, reference_binary = Path(directory), Path(reference_binary)
    began = time.monotonic()
    source_sha256 = digest(regular(Path(__file__), 256*1024))
    need(source in ('q1', 'vr444'), 'explicit RRB source contract required')
    need(re.fullmatch('[0-9a-f]{64}', reference_sha256), 'explicit oracle SHA256 required')
    binary = regular(reference_binary, 64*1024*1024)
    need(digest(binary) == reference_sha256, 'scalar oracle binary hash')
    control = directory/'client/codec-test-present.jsonl'
    raw_present = regular(control, 16*1024*1024)
    rows = [json.loads(line) for line in raw_present.splitlines()]
    need(0 < len(rows) <= 6000, 'bounded presentation population required')
    presented = {}
    for row in rows:
        need(type(row) is dict and all(type(row.get(k)) is int and 0 <= row[k] < 2**64
             for k in ('left', 'right', 'left_warp', 'right_warp')), 'presentation identity types')
        need(row['left'] == row['right'] and row['left_warp'] == row['right_warp'], 'incoherent presentation')
        frame, warp = row['left'], row['left_warp']
        need(frame not in presented or presented[frame] == warp, 'changing presented warp')
        presented[frame] = warp
    payloads = sorted((directory/'host').glob('*.payload'))
    need(0 < len(payloads) <= 128, 'bounded nonempty captured payload population required')
    inputs = {str(control): digest(raw_present)}
    pairs, observations = {}, []
    for path in payloads:
        match = re.fullmatch(r'(0|[1-9][0-9]*)-([01])\.payload', path.name)
        need(match is not None, 'canonical payload name')
        frame, eye = map(int, match.groups());need(frame < 2**64, 'frame bound')
        body = regular(path, MAX_PAYLOAD)
        paths = {suffix:path.with_suffix(suffix) for suffix in ('.envelope', '.rgba', '.reference.rgba')}
        outer = regular(paths['.envelope'], MAX_PAYLOAD+48)
        info = envelope(outer, body, frame, eye)
        count = info['width']*info['height']*4
        original = regular(paths['.rgba'], MAX_PIXELS*4)
        reference = regular(paths['.reference.rgba'], MAX_PIXELS*4)
        need(len(original) == len(reference) == count, 'exact source/reference shape')
        captured = None
        client = directory/'client/codec-test'/path.with_suffix('.rgba').name
        if client.exists():
            captured = regular(client, MAX_PIXELS*4);need(len(captured) == count, 'client capture shape')
            inputs[str(client)] = digest(captured)
        if frame in presented:
            need(presented[frame] == info['warp'], 'presented/envelope warp mismatch')
            need(captured is not None, 'presented captured frame missing decoded eye')
        need(eye not in pairs.setdefault(frame, {}), 'duplicate captured eye')
        pairs[frame][eye] = info
        for p, raw in [(path, body),(paths['.envelope'], outer),(paths['.rgba'], original),(paths['.reference.rgba'], reference)]:
            inputs[str(p)] = digest(raw)
        # Re-encode no metadata and use no native worker/GPU decode path. A copy
        # of the exact hashed payload avoids an input-reread race in the oracle.
        with tempfile.TemporaryDirectory(prefix='rrb-payload-audit-') as temp:
            inp, out = Path(temp)/'input.rrb', Path(temp)/'decoded.rgba'
            inp.write_bytes(body)
            # Execute the exact bytes already hashed, not a mutable build path
            # which another compilation could replace and subsequently restore.
            oracle = Path(temp)/'reference'
            oracle.write_bytes(binary)
            oracle.chmod(0o700)
            remaining = 120-(time.monotonic()-began)
            need(remaining > 0, 'scalar audit total deadline')
            run = subprocess.run([str(oracle),str(inp),str(out)],
                                 capture_output=True, timeout=min(30,remaining), check=False)
            need(run.returncode == 0 and not run.stderr and len(run.stdout) <= 1024, 'scalar payload rejected')
            shape = json.loads(run.stdout)
            need(shape == dict(codec='rrb32',width=info['width'],height=info['height'],rgba_bytes=count), 'scalar shape result')
            decoded = regular(out, MAX_PIXELS*4)
            need(len(decoded) == count and decoded == reference, 'scalar decoded payload differs from host reference')
            if source == 'q1':need(decoded == original, 'Q1 payload changed post-foveation source RGBA')
            if captured is not None:need(decoded == captured, 'scalar decoded payload differs from client bytes')
        observations.append(dict(frame=frame, eye=eye, width=info['width'], height=info['height'],
                                 payload_sha256=digest(body), decoded_sha256=digest(decoded),
                                 reference_sha256=digest(reference), source_sha256=digest(original),
                                 client_capture_present=captured is not None, presented=frame in presented,
                                 exact_to_codec_source=decoded == original))
    admitted = 0
    for frame, eyes in pairs.items():
        need(set(eyes) == {0,1}, 'incomplete captured source pair')
        need(all(eyes[0][key] == eyes[1][key] for key in ('width','height','warp')), 'captured stereo metadata mismatch')
        if frame in presented:
            need(sum(row['bytes'] for row in eyes.values()) <= CAP, 'presented capture exceeded stereo envelope cap')
            admitted += 1
    need(admitted > 0, 'no complete captured presented stereo pair')
    need(digest(regular(reference_binary,64*1024*1024)) == reference_sha256, 'oracle changed during audit')
    need(sorted(str(p) for p in (directory/'host').glob('*.payload')) == sorted(str(p) for p in payloads), 'payload population changed')
    for name, sha in inputs.items():need(digest(regular(Path(name),MAX_PAYLOAD+48)) == sha, 'capture changed during audit')
    need(digest(regular(Path(__file__),256*1024)) == source_sha256, 'auditor source changed')
    return dict(schema=1,status='pass',auditor_source_sha256=source_sha256,
                source_contract='post_foveation_rgb_exact' if source == 'q1' else 'decoded_vr444_rgb_exact',
                oracle_sha256=reference_sha256, captured_eye_payloads=len(observations), complete_captured_pairs=len(pairs),
                captured_presented_pairs=admitted, stereo_envelope_cap=CAP, observations=observations,
                input_sha256=inputs, inputs_unchanged=True, headset_accessed=False,
                scope='Sequential scalar reconstruction of exact saved RRB payload/envelope bytes versus host reference/source and available client output. No display, source-app, acquisition-authenticity or latency claim.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--reference-sha256',required=True)
    parser.add_argument('--source',choices=('q1','vr444'),required=True)
    args=parser.parse_args();result=audit(args.directory,args.reference,args.reference_sha256,args.source)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ('status','captured_eye_payloads','captured_presented_pairs')}))
