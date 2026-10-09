#!/usr/bin/env python3
"""Prepare an immutable executable and explicit quality-only application argv."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from audit import PATTERNS, require, integer, shape

def prepare(binary, expected_sha256, bundle, capture, pattern, width, height, frames=900, seconds=30, source_reference_space="LOCAL"):
    binary, bundle, capture = map(Path, (binary, bundle, capture))
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(), 'absolute regular executable required')
    require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_sha256), 'explicit executable SHA256 required')
    require(binary.stat().st_size <= 64*1024*1024, 'executable bound')
    with binary.open('rb') as source: data = source.read(64*1024*1024+1)
    require(len(data) <= 64*1024*1024 and hashlib.sha256(data).hexdigest() == expected_sha256, 'executable bytes differ from supplied digest')
    require(data.startswith(b'\x7fELF'), 'ELF executable required')
    require(bundle.is_absolute() and not bundle.exists() and capture.is_absolute() and not capture.exists(), 'new absolute bundle/capture paths required')
    require(bundle != capture and not bundle.is_relative_to(capture) and not capture.is_relative_to(bundle), 'bundle and capture must be separate')
    require(source_reference_space in ("VIEW", "LOCAL"), "explicit VIEW or LOCAL source space required")
    shape(width, height); require(pattern in PATTERNS, 'unknown pattern')
    integer(frames, 1, 10800); integer(seconds, 1, 120)
    require(Path('/usr/bin/timeout').is_file() and Path('/usr/bin/stdbuf').is_file(), 'bounded launcher dependencies missing')
    bundle.mkdir(); exe = bundle/'quality-stimuli'; exe.write_bytes(data); exe.chmod(0o500)
    argv = ['/usr/bin/timeout', '-k', '2', str(seconds+10), '/usr/bin/stdbuf', '-oL', '-eL', str(exe),
            '--pattern', pattern, '--width', str(width), '--height', str(height),
            '--source-reference-space', source_reference_space, '--frames', str(frames), '--seconds', str(seconds), '--output', str(capture)]
    plan = dict(schema=2, source_reference_space=source_reference_space, profile='application-quality-stimuli', latency_comparable=False,
                source_generation='direct_encoded_srgb_byte_upload', pattern=pattern,
                source_stage='application_swapchain_before_compositor', width=width, height=height,
                frame_limit=frames, seconds_limit=seconds, capture_directory=str(capture),
                application=argv, executable=str(exe), executable_sha256=expected_sha256,
                original_executable=str(binary), gpu_or_runtime_started=False,
                required_native_contract=dict(mode='custom-rrb32', rrb_source='q1', q=1,
                    compression='none', pairing='paired', source_trace=True, quality_capture=True,
                    stereo_envelope_cap=262144, protected_radius_degrees=10.0, gaze_mode='fixed-center'),
                source_fov_order=['left','right','up','down'],
                quality_mapping_fov_order=['left','right','down','up'],
                limitations=['This plan does not launch any process, alter server config, or qualify latency.',
                    'The binary digest must be joined to a separate build/source receipt.',
                    'Root runner must bind argv, run artifacts and before/after executable hashes.',
                    'Post-foveation codec source remains a distinct image stage; no pixel equality to app input is assumed.',
                    'Cap rejection stays visible; no quantizer/format/geometry downgrade is authorized.'])
    (bundle/'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    return plan

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--binary-sha256',required=True)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--capture',type=Path,required=True)
    p.add_argument('--pattern',choices=PATTERNS,required=True);p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True)
    p.add_argument('--frames',type=int,default=900);p.add_argument('--seconds',type=int,default=30)
    p.add_argument('--source-reference-space', choices=('LOCAL','VIEW'), default='LOCAL')
    a=p.parse_args();result=prepare(a.binary,a.binary_sha256,a.bundle,a.capture,a.pattern,a.width,a.height,a.frames,a.seconds,a.source_reference_space)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
