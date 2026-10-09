#!/usr/bin/env python3
"""Inspect saved native elementary streams; this is not a headset pixel oracle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def inspect_stream(path, codec):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames',
                            '-show_streams', '-of', 'json', str(path)],
                           capture_output=True, check=True, timeout=60)
    streams = json.loads(probe.stdout)['streams']
    if len(streams) != 1:
        raise ValueError('expected one elementary video stream')
    stream = streams[0]
    if stream['codec_name'] != codec or (stream['width'], stream['height']) != (576, 640):
        raise ValueError('native codec or geometry mismatch')
    frames = int(stream['nb_read_frames'])
    if frames < 2:
        raise ValueError('not enough decoded frames')
    # At most 60 sampled frames: approximately 64 MiB, independent of file length.
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
                              '-vf', 'select=not(mod(n\\,30))', '-fps_mode', 'vfr',
                              '-frames:v', '60', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                             capture_output=True, check=True, timeout=60)
    import numpy as np
    size = 576 * 640 * 3
    if not decoded.stdout or len(decoded.stdout) % size:
        raise ValueError('invalid sampled RGB output')
    pixels = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(-1, 640, 576, 3)
    samples = []
    for i, rgb in enumerate(pixels):
        channel_range = int((rgb.max(axis=(0, 1)).astype(int) - rgb.min(axis=(0, 1))).max())
        colors = len(np.unique(rgb[::8, ::8].reshape(-1, 3), axis=0))
        samples.append(dict(frame_ordinal=i * 30, channel_range=channel_range,
                            sampled_colors=colors, nonflat=channel_range >= 16 and colors >= 2,
                            rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest()))
    with path.open('rb') as f:
        sha = hashlib.file_digest(f, 'sha256').hexdigest()
    return dict(path=path.name, bytes=path.stat().st_size, sha256=sha,
                codec=stream['codec_name'], profile=stream.get('profile'),
                pixel_format=stream.get('pix_fmt'), width=576, height=640,
                decoded_frames=frames, bytes_per_encoded_eye_frame=path.stat().st_size / frames,
                samples=samples, nonflat_samples=sum(s['nonflat'] for s in samples),
                decode_stderr=decoded.stderr.decode(errors='replace'),
                probe_stderr=probe.stderr.decode(errors='replace'))


def evaluate(directory, mode):
    directory = Path(directory)
    codec = {'native-h264': 'h264', 'native-hevc': 'hevc'}[mode]
    suffix = 'h264' if codec == 'h264' else 'h265'
    paths = [directory / 'host' / f'video-{eye}.{suffix}' for eye in range(2)]
    result = dict(scope='host elementary bitstreams, independently software decoded; not displayed-image quality or network wire bytes',
                  mode=mode, errors=[])
    try:
        result['eyes'] = [inspect_stream(p, codec) for p in paths]
        if any(not eye['nonflat_samples'] for eye in result['eyes']):
            result['errors'].append('missing nonflat content in an eye')
        result['mean_bytes_per_encoded_stereo_pair'] = sum(e['bytes_per_encoded_eye_frame'] for e in result['eyes'])
        result['quality_against_source'] = 'unmeasured'
        result['displayed_pixel_correctness'] = 'unmeasured'
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result['errors'].append(str(exc))
    result['status'] = 'pass' if not result['errors'] else 'fail'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--mode', choices=['native-h264', 'native-hevc'], required=True)
    args = parser.parse_args()
    result = evaluate(args.directory, args.mode)
    (args.directory / 'native-bitstream-evidence.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(result['status'] != 'pass')
