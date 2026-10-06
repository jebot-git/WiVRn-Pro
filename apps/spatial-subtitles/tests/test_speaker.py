# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from local_speaker import SpeakerTracker


def vector(index):
    return [float(i == index) for i in range(256)]


class SpeakerTests(unittest.TestCase):
    def test_repeated_different_and_capacity(self):
        tracker = SpeakerTracker()
        self.assertEqual(
            [tracker.assign(vector(i)) for i in (0, 1, 0, 2, 3, 4, 1)],
            [1, 2, 1, 3, 4, 0, 2],
        )

    def test_invalid_or_uncertain_is_neutral(self):
        tracker = SpeakerTracker()
        for value in (None, [], [0] * 256, [float("nan")] * 256):
            self.assertEqual(tracker.assign(value), 0)
        tracker.assign(vector(0))
        tracker.assign(vector(1))
        self.assertEqual(tracker.assign([2**-0.5, 2**-0.5] + [0] * 254), 0)


if __name__ == "__main__":
    unittest.main()
