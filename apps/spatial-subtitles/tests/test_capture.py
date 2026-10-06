# SPDX-License-Identifier: GPL-3.0-or-later
import os, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import capture


class CaptureTests(unittest.TestCase):
    def frame(self, root, age=0, version=2, mode=0o600, size=16):
        p = root / "test.frame"
        h = capture.HEADER.pack(
            0x4F58524D,
            version,
            2,
            2,
            2,
            0,
            8,
            os.getpid(),
            1,
            time.monotonic_ns() - int(age * 1e9),
            -0.8,
            0.8,
            0.8,
            -0.8,
        )
        p.write_bytes(h + bytes(size))
        p.chmod(mode)
        return p

    def test_private_valid_frame_and_rejections(self):
        try:
            __import__("numpy")
            __import__("cv2")
        except ImportError:
            self.skipTest("Run capture tests in the standalone inference environment")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            with patch.object(capture, "DIRECTORY", root):
                self.frame(root)
                f = capture.latest()
                self.assertEqual(f["image"].shape, (2, 2, 3))
                self.assertEqual(f["source_pid"], os.getpid())
                for kwargs in [
                    {"age": 2},
                    {"version": 1},
                    {"mode": 0o644},
                    {"size": 8},
                ]:
                    self.frame(root, **kwargs)
                    self.assertIsNone(capture.latest())
                p = self.frame(root)
                p.unlink()
                p.symlink_to(root / "missing")
                self.assertIsNone(capture.latest())


if __name__ == "__main__":
    unittest.main()
