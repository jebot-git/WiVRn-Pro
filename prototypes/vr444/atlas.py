#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""RGB444 carried by the luma of ordinary HEVC420; offline proof of packing.

This tests a custom pixel layout, NOT native HEVC444. No spatial chroma samples
are discarded. R, G, B occupy three vertical full-resolution luma panels.
Carrier U/V are neutral. Access decoded luma directly: do not colour-convert it.
Requires numpy and ffmpeg/libx265. Software quality checks are NOT QSV timings.
"""
import argparse
import json
import pathlib
import subprocess
import numpy as np


def pack_rgb(rgb):
    h, w, components = rgb.shape
    if components != 3 or w % 2 or h % 2 or rgb.dtype != np.uint8:
        raise ValueError("even RGB8 dimensions required")
    luma = np.concatenate([rgb[:, :, c] for c in range(3)], axis=0)
    return luma.tobytes() + bytes([128]) * (w * h * 3 // 2)


def unpack_rgb(carrier, w, h):
    if len(carrier) != w * h * 9 // 2:
        raise ValueError("carrier size")
    luma = np.frombuffer(carrier, np.uint8, count=w * h * 3).reshape(3, h, w)
    return luma.transpose(1, 2, 0).copy()


def picture(w, h, frame):
    y, x = np.indices((h, w))
    x = (x + frame * 3) % w
    line = (x % 37 == 0) | (y % 29 == 0)
    rgb = np.empty((h, w, 3), np.uint8)
    for c, (a, b) in enumerate(((255, 30), (16, 40), (32, 50))):
        rgb[:, :, c] = np.where(line, a, b)
    for c, (a, b) in enumerate(((255, 0), (0, 130), (0, 255))):
        rgb[:h//5, :, c] = np.where(x[:h//5] & 1, a, b)
    return rgb


def run(out, frames, encoder="libx265", device="/dev/dri/renderD129"):
    out.mkdir(parents=True, exist_ok=True)
    w, h = 1152, 640  # Both 576x640 eyes, side by side. 1152x1920 carrier.
    source = out / "atlas-input.yuv"
    with source.open("wb") as f:
        for i in range(frames):
            rgb = picture(w, h, i)
            data = pack_rgb(rgb)
            assert np.array_equal(rgb, unpack_rgb(data, w, h))
            f.write(data)
    # One-pixel colour/ramp preservation, including endpoints.
    ramp = np.arange(256, dtype=np.uint8).reshape(16, 16)
    ramp = np.stack([ramp, np.flip(ramp, 0), np.flip(ramp, 1)], axis=2)
    assert np.array_equal(ramp, unpack_rgb(pack_rgb(ramp), 16, 16))
    results = []
    for mbps in (50, 100, 150):
        encoded, decoded = out / f"atlas-{mbps}.hevc", out / f"atlas-{mbps}.yuv"
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        if encoder == "hevc_qsv":
            args += ["-init_hw_device", f"vaapi=va:{device}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"]
        args += ["-f", "rawvideo",
                "-pixel_format", "yuv420p", "-video_size", f"{w}x{h*3}", "-framerate", "90",
                "-color_range", "pc", "-i", str(source), "-frames:v", str(frames)]
        if encoder == "libx265":
            args += ["-c:v", "libx265", "-preset", "ultrafast", "-tune", "zerolatency", "-x265-params",
                     "log-level=error:pools=4:frame-threads=1:bframes=0:rc-lookahead=0:keyint=90:scenecut=0:range=full"]
        else:
            args += ["-vf", "format=nv12,hwupload=extra_hw_frames=2", "-c:v", "hevc_qsv", "-preset", "veryfast",
                     "-async_depth", "1", "-bf", "0", "-gpb", "0", "-g", "90", "-look_ahead_depth", "0",
                     "-extbrc", "0", "-low_delay_brc", "1"]
        args += ["-b:v", f"{mbps}M", "-maxrate", f"{mbps}M", "-bufsize", f"{round(mbps*1000/90)}k",
                 "-color_range", "pc", "-f", "hevc", str(encoded)]
        subprocess.run(args, check=True, stderr=(out / f"atlas-{mbps}-encode.log").open("w"))
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(encoded),
                        "-pix_fmt", "yuvj420p", "-f", "rawvideo", str(decoded)], check=True)
        size = w*h*9//2
        squared = 0
        with decoded.open("rb") as f:
            for i in range(frames):
                rgb = unpack_rgb(f.read(size), w, h).astype(np.float64)
                squared += np.square(rgb-picture(w, h, i)).sum()
            assert not f.read(1)
        psnr = 999 if squared == 0 else 10*np.log10(255**2 * frames*w*h*3 / squared)
        results.append(dict(carrier="RGB-in-luma HEVC420", encoder=encoder, software_encoder=encoder == "libx265",
                            frames=frames, eye_width=576, eye_height=h, fps=90, carrier_width=w, carrier_height=h*3,
                            target_mbps=mbps, actual_payload_mbps=encoded.stat().st_size*8*90/frames/1e6,
                            rgb_psnr_db=float(psnr), command=args))
        decoded.unlink()  # Encoded samples and exact regenerable source recipe remain.
    source.unlink()
    (out / "atlas-results.json").write_text(json.dumps(results, indent=2)+"\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("output", type=pathlib.Path)
    p.add_argument("--frames", type=int, default=30)
    p.add_argument("--encoder", choices=("libx265", "hevc_qsv"), default="libx265")
    p.add_argument("--device", default="/dev/dri/renderD129")
    a = p.parse_args()
    if not 1 <= a.frames <= 900:
        p.error("frames must be 1..900")
    run(a.output, a.frames, a.encoder, a.device)
