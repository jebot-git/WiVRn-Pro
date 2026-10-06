# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the opt-in layer's private, bounded, versioned RGBA transport."""

import fcntl, os, stat, struct, time
from pathlib import Path

HEADER = struct.Struct("<8I2Q4f")
DIRECTORY = Path(
    os.environ.get(
        "SPATIAL_SUBTITLES_CAPTURE_DIR",
        str(
            Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
            / f"spatial-subtitles-capture-{os.getuid()}"
        ),
    )
)


def latest():
    import numpy as np, cv2

    if not DIRECTORY.exists():
        return None
    info = DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        return None
    try:
        paths = sorted(
            DIRECTORY.glob("*.frame"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:4]
    except OSError:
        return None
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as f:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                st = os.fstat(f.fileno())
                if (
                    not stat.S_ISREG(st.st_mode)
                    or st.st_uid != os.getuid()
                    or st.st_mode & 0o077
                ):
                    continue
                header = f.read(HEADER.size)
                if len(header) != HEADER.size:
                    continue
                magic, version, w, h, left, right, stride, pid, seq, stamp, *fov = (
                    HEADER.unpack(header)
                )
                if (
                    magic != 0x4F58524D
                    or version != 2
                    or not (0 < left <= w <= 16384 and 0 < h <= 8192)
                    or left + right != w
                    or stride != w * 4
                    or w * h * 4 > 128 * 1024**2
                ):
                    continue
                if (
                    st.st_size != HEADER.size + w * h * 4
                    or not 0 <= time.monotonic_ns() - stamp < 1_000_000_000
                ):
                    continue
                if (
                    not all(abs(x) < 1.57 for x in fov)
                    or fov[0] >= fov[1]
                    or fov[3] >= fov[2]
                ):
                    continue
                raw = f.read(w * h * 4)
                fcntl.flock(f, fcntl.LOCK_UN)
            image = np.frombuffer(raw, np.uint8).reshape(h, w, 4)[:, :left, :3].copy()
            scale = min(1, 1024 / max(image.shape[:2]))
            image = cv2.resize(image, (int(left * scale), int(h * scale)))
            return dict(
                image=image, fov=fov, captured=stamp / 1e9, source_pid=pid, sequence=seq
            )
        except (OSError, ValueError):
            continue
    return None
