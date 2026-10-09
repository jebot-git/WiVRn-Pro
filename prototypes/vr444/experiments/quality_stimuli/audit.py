#!/usr/bin/env python3
"""Offline application-source artifact audit; never supplies a client reference."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

PATTERNS = ('chroma-grid', 'rgb-ramp', 'fine-detail')

def require(value, message):
    if not value:
        raise ValueError(message)

def integer(value, low, high):
    require(type(value) is int and low <= value <= high, 'invalid bounded integer')
    return value

def shape(width, height):
    integer(width, 1, 4096); integer(height, 1, 4096)
    require(width * height <= 4194304, 'pixel cap')

def expected(pattern, width, height, eye):
    """Independent Python specification of application encoded-SRGB pixels."""
    require(pattern in PATTERNS, 'unknown pattern'); shape(width, height); integer(eye, 0, 1)
    colors = ((255,0,0), (0,255,255), (0,255,0), (255,0,255),
              (0,0,255), (255,255,0), (32,224,96), (224,32,160))
    result = bytearray(width * height * 4)
    for row in range(height):
        for col in range(width):
            source_col = width - col - 1 if eye else col
            if pattern == 'chroma-grid':
                band = min(3, (4 * row) // height)
                size = 2 ** band
                phase = ((source_col // size) + (row // size)) % 2
                red, green, blue = colors[2 * band + phase]
            elif pattern == 'rgb-ramp':
                red, green, blue = source_col % 256, row % 256, (source_col + 3 * row) % 256
            else:
                quadrant = int(2 * source_col >= width) + 2 * int(2 * row >= height)
                size = 2 ** quadrant
                red, green, blue = (255 * ((source_col // size) % 2),
                                    255 * ((row // size) % 2),
                                    255 * (((source_col + row) // size) % 2))
            if eye:
                red, green, blue = blue, red, green
            if col == 0:
                red, green, blue = (17 if eye else 239), 31, 73
            elif col == width - 1:
                red, green, blue = 47, (223 if eye else 19), 101
            elif row == 0:
                red, green, blue = 61, 113, (197 if eye else 29)
            elif row == height - 1:
                red, green, blue = (181 if eye else 43), 149, 83
            start = (row * width + col) * 4
            result[start:start+4] = bytes((red, green, blue, 255))
    return bytes(result)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)

def load_file(directory, name, limit, snapshots):
    require(type(name) is str and Path(name).name == name, 'unsafe artifact name')
    path = directory / name
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit, 'invalid artifact file')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    require(len(data) <= limit, 'file grew beyond bound')
    if name in snapshots:
        require(snapshots[name] == data, 'input changed during audit')
    snapshots[name] = data
    return data

def finish(directory, snapshots):
    for name, data in snapshots.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'input changed after audit')
        with path.open('rb') as stream:
            require(stream.read(len(data)+1) == data, 'input changed after audit')
    require(set(p.name for p in directory.iterdir()) == set(snapshots), 'unbound extra artifact')
    return {name: sha(data) for name, data in sorted(snapshots.items())}

def fixtures(directory):
    directory = Path(directory); snapshots = {}
    meta = strict_json(load_file(directory, 'fixtures.json', 65536, snapshots))
    require(type(meta.get('schema')) is int and meta['schema'] == 1 and meta.get('source_generation') == 'cpu_fixture_only', 'fixture source scope')
    require(meta.get('openxr_exercised') is False and meta.get('gpu_exercised') is False, 'fixture execution scope')
    shape(meta.get('width'), meta.get('height')); require(meta.get('pattern') in PATTERNS, 'pattern')
    require(type(meta.get('eyes')) is list and len(meta['eyes']) == 2, 'stereo fixture count')
    for eye, record in enumerate(meta['eyes']):
        require(type(record.get('eye')) is int and record['eye'] == eye, 'eye identity')
        data = load_file(directory, f'eye{eye}.source.rgba', 16777216, snapshots)
        require(record.get('file') == f'eye{eye}.source.rgba' and record.get('bytes') == len(data) and record.get('sha256') == sha(data), 'fixture hash/size')
        require(data == expected(meta['pattern'], meta['width'], meta['height'], eye), 'independent source oracle mismatch')
    return {'status': 'pass', 'mode': 'cpu_fixture_only', 'eyes': 2, 'input_sha256': finish(directory, snapshots)}

def vector(record, name, count):
    require(type(record) is dict, 'pose/FOV object')
    values = record.get(name); bits = record.get(name + '_bits' if name != 'radians' else 'bits')
    require(type(values) is list and type(bits) is list and len(values) == len(bits) == count, 'float vector shape')
    for value, encoded in zip(values, bits):
        require(type(value) in (int, float) and math.isfinite(value), 'nonfinite float')
        integer(encoded, 0, 2**32-1)
        require(struct.unpack('<I', struct.pack('<f', value))[0] == encoded, 'float bits disagree')
    return values

def gate_decision(row):
    require(type(row.get('should_render')) is bool and type(row.get('locate_called')) is bool, 'typed frame gate')
    flags = integer(row.get('view_state_flags'), 0, 15)
    require(row['locate_called'] == row['should_render'], 'locate/shouldRender mismatch')
    if not row['should_render']:
        require(flags == 0, 'unlocated frame carries pose validity')
        expected_decision = 'should_render_false'
    else:
        expected_decision = 'admitted' if flags & 3 == 3 else 'view_pose_invalid'
    require(row.get('decision') == expected_decision, 'forged tracking gate decision')
    return expected_decision

def application(directory):
    directory = Path(directory); snapshots = {}
    raw = load_file(directory, 'app.jsonl', 32 * 1024 * 1024, snapshots)
    rows = [strict_json(line) for line in raw.splitlines()]
    require(4 <= len(rows) <= 22000, 'app record count')
    require(all(type(row) is dict for row in rows), 'record object required')
    head, tail = rows[0], rows[-1]
    require(head.get('type') == 'header' and type(head.get('schema')) is int and head['schema'] in (1,2) and tail.get('type') == 'footer', 'header/footer order')
    require(head.get('source_generation') == 'direct_encoded_srgb_byte_upload' and head.get('source_stage') == 'application_swapchain_before_compositor', 'source stage')
    require(head.get('pattern') in PATTERNS and head.get('space') in ('VIEW','LOCAL'), 'pattern/reference space')
    if head['schema']==1:
        require(head['space']=='VIEW' and 'source_reference_space' not in head, 'legacy VIEW source cannot be relabeled')
    else:
        require(head.get('source_reference_space')==head['space'] and head.get('locate_and_layer_use_same_space') is True,'source space contract')
    require(head.get('quality_only') is True and head.get('latency_comparable') is False and head.get('opaque') is True and head.get('top_left_origin') is True, 'profile scope')
    require(head.get('format') == 43 and head.get('format_name') == 'R8G8B8A8_SRGB', 'actual swapchain format')
    limit = integer(head.get('frame_limit'), 1, 10800); integer(head.get('seconds_limit'), 1, 120)
    require(tail.get('complete') is True and tail.get('quality_only') is True and tail.get('latency_comparable') is False, 'complete quality footer')
    require(tail.get('stop_reason') in ('time_limit', 'frame_limit', 'runtime_terminal') and type(tail.get('session_ended')) is bool, 'session termination record')
    eye_records = {}; capture_records = {}; frames = []; captures_seen = set(); submitted = 0; gates = {}
    for row in rows[1:-1]:
        kind = row.get('type')
        if kind == 'eye':
            eye = integer(row.get('eye'), 0, 1); require(eye not in eye_records and not frames, 'duplicate/late eye setup')
            shape(row.get('width'), row.get('height')); integer(row.get('swapchain_images'), 1, 16)
            width, height = row['width'], row['height']
            for name, actual in (('width', width), ('height', height)):
                requested = integer(head.get(name + '_requested'), 0, 4096)
                require(actual == (requested or row.get('recommended_' + name)), 'requested/recommended shape mismatch')
            data = load_file(directory, f'eye{eye}.source.rgba', 16777216, snapshots)
            require(row.get('source_file') == f'eye{eye}.source.rgba' and row.get('source_bytes') == len(data) and row.get('source_sha256') == sha(data), 'source artifact metadata')
            require(data == expected(head['pattern'], width, height, eye), 'original app source oracle mismatch')
            eye_records[eye] = row
        elif kind == 'image_capture':
            eye = integer(row.get('eye'), 0, 1); require(eye in eye_records, 'capture before setup')
            image = integer(row.get('image_index'), 0, eye_records[eye]['swapchain_images']-1); key = eye, image
            require(key not in capture_records, 'duplicate image capture')
            name = f'eye{eye}-image{image}.readback.rgba'; data = load_file(directory, name, 16777216, snapshots)
            require(row.get('file') == name and row.get('bytes') == len(data) and row.get('sha256') == sha(data) and row.get('exact_source') is True, 'readback artifact metadata')
            require(data == snapshots[f'eye{eye}.source.rgba'], 'actual app readback differs from source')
            capture_records[key] = row; captures_seen.add(key)
        elif kind == 'frame':
            if head['schema']==2:require(row.get('source_reference_space')==head['source_reference_space'],'per-frame reference space mismatch')
            require(set(eye_records) == {0, 1}, 'frame before stereo setup')
            require(type(row.get('sequence')) is int and row['sequence'] == len(frames)+1, 'frame sequence')
            integer(row.get('predicted_display_time'), 1, 2**63-1); integer(row.get('predicted_display_period'), 1, 10**10)
            require(not frames or row['predicted_display_time'] > frames[-1]['predicted_display_time'], 'nonincreasing app target')
            decision = gate_decision(row)
            if row['sequence'] <= 32:
                require(row['sequence'] in gates and all(row[k] == gates[row['sequence']][k]
                    for k in ('should_render', 'locate_called', 'view_state_flags', 'decision')), 'missing/mismatched early frame gate')
            require(type(row.get('should_render')) is bool and type(row.get('projection_submitted')) is bool and type(row.get('views')) is list, 'frame flags')
            require(row['projection_submitted'] == (decision == 'admitted'), 'projection ignored validity gate')
            if row['projection_submitted']:
                require(row['should_render'] and len(row['views']) == 2, 'submitted stereo views')
                for eye, view in enumerate(row['views']):
                    require(type(view.get('eye')) is int and view['eye'] == eye, 'submitted eye order')
                    image = integer(view.get('image_index'), 0, eye_records[eye]['swapchain_images']-1)
                    require((eye, image) in captures_seen, 'submitted image lacks prior actual capture')
                    for field in ('width', 'height', 'source_sha256'):
                        require(view.get(field) == eye_records[eye][field], 'submitted source identity')
                    require(view.get('readback_exact') is True, 'missing exact actual image check')
                    orient = vector(view.get('pose'), 'orientation', 4); vector(view['pose'], 'position', 3)
                    require(abs(sum(x*x for x in orient)-1) < .01, 'invalid view quaternion')
                    angles = vector(view.get('fov'), 'radians', 4)
                    require(-math.pi/2 < angles[0] < angles[1] < math.pi/2 and -math.pi/2 < angles[3] < angles[2] < math.pi/2, 'invalid FOV')
                submitted += 1
            else:
                require(row['views'] == [], 'unsubmitted frame has submitted views')
            frames.append(row)
        elif kind == 'frame_gate':
            sequence = integer(row.get('sequence'), 1, 32)
            require(sequence == len(frames)+1 and sequence not in gates, 'misordered early frame gate')
            require(type(row.get('required_valid_flags')) is int and row['required_valid_flags'] == 3, 'validity requirement changed')
            integer(row.get('session_state'), 1, 8); gate_decision(row); gates[sequence] = row
        elif kind == 'session_state':
            integer(row.get('state'), 1, 8); integer(row.get('xr_time'), 0, 2**63-1)
        else:
            raise ValueError('unknown/misordered app record')
    require(type(tail.get('frames')) is int and type(tail.get('submitted')) is int
            and 0 < submitted <= len(frames) <= limit and tail['frames'] == len(frames) and tail['submitted'] == submitted, 'frame/footer population')
    require(set(gates) == set(range(1, min(len(frames), 32)+1)), 'early frame gate population')
    return {'schema': 1, 'status': 'pass', 'profile': 'application_quality_source',
            'source_reference_space': head['space'], 'source_application_space': head['space'],
            'pattern': head['pattern'], 'frames': len(frames), 'submitted_stereo_frames': submitted,
            'captured_swapchain_images': len(capture_records), 'session_ended': tail['session_ended'],
            'source_stage': head['source_stage'], 'source_generation': head['source_generation'],
            'source_sha256': [eye_records[e]['source_sha256'] for e in (0, 1)],
            'original_application_source_exact': True, 'post_foveation_comparison_performed': False,
            'codec_or_client_comparison_performed': False, 'latency_comparable': False,
            'limits': ['Execution provenance must separately bind the app binary and command.',
                       'This verifies saved app-image bytes and declared frame records; no display or codec quality result.',
                       'Actual pose/FOV/target records are retained for a separate source/compositor join.'],
            'input_sha256': finish(directory, snapshots)}

def main():
    p = argparse.ArgumentParser(); p.add_argument('directory', type=Path); p.add_argument('output', type=Path)
    p.add_argument('--fixtures-only', action='store_true'); a = p.parse_args()
    result = fixtures(a.directory) if a.fixtures_only else application(a.directory)
    with a.output.open('x') as out: json.dump(result, out, indent=2); out.write('\n')

if __name__ == '__main__': main()
