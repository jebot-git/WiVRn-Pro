# SPDX-License-Identifier: MIT
import pathlib
import subprocess
import tempfile
import unittest
import numpy as np
from atlas import pack_rgb, unpack_rgb


class AtlasTests(unittest.TestCase):
    def test_pixel_exact_ramp_and_one_pixel_chroma(self):
        rng = np.random.default_rng(44)
        rgb = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
        self.assertTrue(np.array_equal(rgb, unpack_rgb(pack_rgb(rgb), 32, 32)))
        with self.assertRaises(ValueError):
            pack_rgb(rgb[:, :31])
        with self.assertRaises(ValueError):
            unpack_rgb(pack_rgb(rgb)[:-1], 32, 32)

    def test_hevc_lossless_carrier_preserves_all_levels(self):
        # This is a software transport correctness test, not an available QSV preset.
        ramp = np.arange(1024, dtype=np.uint16).reshape(32, 32).astype(np.uint8)
        rgb = np.stack([ramp, np.flip(ramp, 0), np.flip(ramp, 1)], axis=2)
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "in.yuv").write_bytes(pack_rgb(rgb))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pixel_format", "yuv420p",
                            "-video_size", "32x96", "-color_range", "pc", "-i", str(root / "in.yuv"), "-frames:v", "1",
                            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
                            "log-level=error:pools=1:lossless=1:range=full", str(root / "out.hevc")], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / "out.hevc"),
                            "-pix_fmt", "yuvj420p", "-f", "rawvideo", str(root / "out.yuv")], check=True)
            actual = unpack_rgb((root / "out.yuv").read_bytes(), 32, 32)
            self.assertTrue(np.array_equal(rgb, actual), "a colour-range conversion corrupted the luma carrier")


if __name__ == "__main__":
    unittest.main()
