#!/usr/bin/env python3
"""Exact RGB evidence primitives for the fixed central 10-degree research target.

This module compares real bytes and measured projection footprints. It does not
turn codec output, synthetic fixtures, or an offscreen test into display evidence.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

RADIUS_DEGREES = 10.0
MAX_PIXELS = 4_194_304
STAGES = ('decoded-source', 'sampled-image', 'submitted-uncompressed-reference')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def shape(width, height):
    require(type(width) is int and type(height) is int
            and 1 <= width <= 4096 and 1 <= height <= 4096
            and width * height <= MAX_PIXELS, 'invalid per-eye geometry')


def perspective_edges(width, height, fov):
    """Unwarped projection only; FOV is left/right/down/up in radians.

    Spatially warped codec images must instead supply their actual ray edges.
    Image rows run from up to down. No guessed screen-space radius is accepted.
    """
    shape(width, height)
    require(type(fov) in (list, tuple) and len(fov) == 4, 'four FOV angles required')
    require(all(type(v) in (int, float) and math.isfinite(v)
                and -math.pi/2 < v < math.pi/2 for v in fov), 'invalid FOV')
    left, right, down, up = fov
    require(left < 0 < right and down < 0 < up, 'FOV must contain central axis')
    return (np.linspace(math.tan(left), math.tan(right), width+1),
            np.linspace(math.tan(up), math.tan(down), height+1))


def protected_mask(width, height, x_ray_edges, y_ray_edges):
    """Conservatively include every footprint intersecting the central cone.

    x/y are measured separable projection footprint bounds, in tangent units.
    For nonseparable warps a different measured-footprint adapter is required.
    Including boundary pixels prevents hiding errors in partial foveal pixels.
    """
    shape(width, height)
    x = np.asarray(x_ray_edges, dtype=np.float64)
    y = np.asarray(y_ray_edges, dtype=np.float64)
    require(x.shape == (width+1,) and y.shape == (height+1,), 'ray edge count')
    require(np.isfinite(x).all() and np.isfinite(y).all(), 'nonfinite ray edges')
    require((np.diff(x) > 0).all() and (np.diff(y) < 0).all(), 'ray edges must be monotonic')
    radius = math.tan(math.radians(RADIUS_DEGREES))
    require(x[0] <= -radius and x[-1] >= radius
            and y[-1] <= -radius and y[0] >= radius, 'capture clips the central 10-degree cone')

    def distance_to_interval(a, b):
        return np.where((np.minimum(a, b) <= 0) & (np.maximum(a, b) >= 0),
                        0.0, np.minimum(np.abs(a), np.abs(b)))

    dx = distance_to_interval(x[:-1], x[1:])
    dy = distance_to_interval(y[:-1], y[1:])
    result = dx[None, :]**2 + dy[:, None]**2 <= radius**2
    require(result.any(), 'empty protected region')
    return result


def compare_rgba(reference, candidate, width, height, mask):
    """RGB byte equality; alpha, color conversion and geometry are separate.

    The exact read buffers, not a second read of their paths, are hashed below.
    Full-image equality is also reported, even when only the fovea is exact.
    """
    shape(width, height)
    require(type(reference) is bytes and type(candidate) is bytes, 'immutable capture bytes required')
    require(len(reference) == len(candidate) == width*height*4, 'RGBA byte count')
    require(isinstance(mask, np.ndarray) and mask.dtype == np.bool_
            and mask.shape == (height, width) and mask.any(), 'nonempty boolean region required')
    a = np.frombuffer(reference, dtype=np.uint8).reshape(height, width, 4)
    b = np.frombuffer(candidate, dtype=np.uint8).reshape(height, width, 4)
    delta = np.abs(a[:, :, :3].astype(np.int16)-b[:, :, :3].astype(np.int16))
    protected = delta[mask]
    return {
        'protected_pixels': int(mask.sum()),
        'protected_exact': bool((protected == 0).all()),
        'protected_changed_pixels': int(np.any(protected != 0, axis=1).sum()),
        'protected_max_absolute_code_error': int(protected.max()),
        'full_rgb_exact': bool((delta == 0).all()),
        'full_rgb_changed_pixels': int(np.any(delta != 0, axis=2).sum()),
        'opaque_alpha_reference': bool((a[:, :, 3] == 255).all()),
        'opaque_alpha_candidate': bool((b[:, :, 3] == 255).all()),
        'reference_sha256': hashlib.sha256(reference).hexdigest(),
        'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
        'mask_sha256': hashlib.sha256(mask.tobytes()).hexdigest(),
        'scope': 'Exact RGB at the explicitly identified capture boundary; no physical display inference',
    }


class BoundInputs:
    """Read each declared file once, bind its actual bytes, reject escaping paths."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.reads = {}

    def read(self, descriptor, max_bytes=MAX_PIXELS*4):
        require(type(descriptor) is dict and set(descriptor) == {'path', 'sha256'}, 'file descriptor fields')
        name, expected = descriptor['path'], descriptor['sha256']
        require(type(name) is str and name and not Path(name).is_absolute()
                and '..' not in Path(name).parts, 'invalid relative path')
        path = self.root/name
        require(not path.is_symlink() and path.resolve().is_relative_to(self.root), 'file escapes evidence root')
        require(path.is_file() and path.stat().st_size <= max_bytes, 'missing or oversized evidence')
        with path.open('rb') as stream:
            data = stream.read(max_bytes+1)
        require(len(data) <= max_bytes, 'oversized read')
        actual = hashlib.sha256(data).hexdigest()
        require(type(expected) is str and actual == expected, 'evidence hash mismatch: '+name)
        previous = self.reads.get(name)
        record = {'bytes': len(data), 'sha256': actual}
        require(previous is None or previous == record, 'evidence changed between uses: '+name)
        self.reads[name] = record
        return data

    def json(self, descriptor, max_bytes=4*1024*1024):
        def reject_constant(value):
            raise ValueError('nonfinite JSON constant: '+value)
        return json.loads(self.read(descriptor, max_bytes), parse_constant=reject_constant)


def compare_capture(inputs, capture):
    """One capture comparison; full stereo/native qualification is external.

    Projection provenance must be supplied by the acquisition adapter. The
    measured mask geometry and reference boundary are retained in the result.
    """
    require(capture.get('stage') in STAGES, 'unknown capture boundary')
    require(capture.get('channel_format') == 'RGBA8', 'unsupported channel format')
    require(capture.get('reference_boundary') in (
        'original-application-rgb', 'post-host-foveation-rgb',
        'uncompressed-same-render'), 'explicit reference boundary required')
    if capture['stage'] == 'submitted-uncompressed-reference':
        require(capture['reference_boundary'] == 'uncompressed-same-render', 'final render needs a matched raw reference')
    require(type(capture.get('frame')) is int and capture['frame'] >= 0
            and type(capture.get('warp')) is int and capture['warp'] >= 0
            and type(capture.get('eye')) is int and capture['eye'] in (0, 1), 'capture identity')
    width, height = capture['geometry']
    mapping = inputs.json(capture['projection'])
    require(mapping.get('geometry') == [width, height], 'projection geometry mismatch')
    require(mapping.get('frame') == capture['frame'] and mapping.get('warp') == capture['warp']
            and mapping.get('eye') == capture['eye'], 'projection identity mismatch')
    if mapping.get('kind') == 'unwarped-perspective':
        x, y = perspective_edges(width, height, mapping['fov_radians'])
    elif mapping.get('kind') == 'measured-separable-ray-edges':
        x, y = mapping['x_ray_edges'], mapping['y_ray_edges']
    else:
        raise ValueError('actual projection/warp mapping required')
    mask = protected_mask(width, height, x, y)
    reference = inputs.read(capture['reference'])
    candidate = inputs.read(capture['candidate'])
    result = compare_rgba(reference, candidate, width, height, mask)
    return {key: capture[key] for key in ('stage', 'reference_boundary', 'frame', 'warp', 'eye', 'geometry')} | result | {
        'status': 'pass' if result['protected_exact'] else 'fail',
        'protected_radius_degrees': RADIUS_DEGREES, 'gaze_mode': 'fixed-center',
        'full_chroma_path_verified': False, 'headset_execution_verified': False,
        'submitted_pixel_equality_verified': False, 'visual_artifacts_assessed': False,
        'projection_input': dict(capture['projection']),
        'reference_input': dict(capture['reference']), 'candidate_input': dict(capture['candidate']),
        'read_input_manifest': dict(inputs.reads),
        'sampling_scope': 'Mask uses pixel-cell footprints; filtered samples additionally need matching filter footprints and transfer encoding from the acquisition adapter',
        'full_goal_qualified': False,
    }


def research_decision(latency, quality):
    """Combine independently evaluated routes, not acquisition measurements.

    Callers must supply native route validators' reports; a component comparator
    explicitly cannot set goal_verified. Missing/failed opposite routes do not
    veto an achieved goal. This never changes a production promotion policy.
    """
    def achieved(report):
        return (type(report) is dict and report.get('status') == 'pass'
                and report.get('goal_verified') is True
                and report.get('evidence_integrity_verified') is True)
    passed = [name for name, report in (('latency', latency), ('quality', quality)) if achieved(report)]
    return {'qualified_for_further_research': bool(passed), 'qualified_routes': passed,
            'operator': 'OR', 'latency': latency, 'quality': quality,
            'promotion_eligible': False, 'release_ready': False,
            'scope': 'Combines validated route reports; does not validate measurements or establish display photons'}
