# SPDX-License-Identifier: GPL-3.0-or-later
from array import array
from collections import deque
from dataclasses import dataclass
import math, sys

RATE = 16000
FRAME_SAMPLES = 320
FRAME_BYTES = 1280


def direction(pans, enabled):
    pans = [p for p in pans if p is not None]
    if not pans:
        return 0.0, False
    pan = sum(pans) / len(pans)
    deviation = math.sqrt(sum((p - pan) ** 2 for p in pans) / len(pans))
    return max(-1.0, min(1.0, pan)) * math.pi / 3, bool(
        enabled and abs(pan) >= 0.18 and deviation < 0.18
    )


@dataclass
class Segment:
    pcm: bytes
    captured: float
    pans: list
    anchor: dict | None = None


class Segmenter:
    """Bounded energy gate, NOT speaker separation or a claim of speech detection.

    ASR decides what words are present. Direction uses stable channel imbalance
    over the utterance; ambiguous/moving/multiple-source mixtures are centered.
    """

    def __init__(self, settings):
        self.settings = settings
        self.pre = deque(maxlen=10)
        self.frames = []
        self.pans = []
        self.quiet = self.voiced = 0
        self.start = 0.0

    def feed(self, data, now):
        if len(data) != FRAME_BYTES:
            raise ValueError("Expected one stereo audio frame")
        stereo = array("h")
        stereo.frombytes(data)
        if sys.byteorder != "little":
            stereo.byteswap()
        left = sum(x * x for x in stereo[::2]) / FRAME_SAMPLES
        right = sum(x * x for x in stereo[1::2]) / FRAME_SAMPLES
        active = math.sqrt((left + right) / 2) / 32768 >= self.settings.threshold
        mono = array(
            "h",
            (int((stereo[i] + stereo[i + 1]) / 2) for i in range(0, len(stereo), 2)),
        )
        if sys.byteorder != "little":
            mono.byteswap()
        pcm = mono.tobytes()
        if not self.frames:
            if not active:
                self.pre.append(pcm)
                return None
            self.frames = list(self.pre)
            self.pre.clear()
            self.pans = [None] * len(self.frames)
            self.start = now - len(self.frames) * 0.02
        self.frames.append(pcm)
        if active:
            self.voiced += 1
            self.quiet = 0
            self.pans.append(
                (math.sqrt(right) - math.sqrt(left))
                / max(1.0, math.sqrt(right) + math.sqrt(left))
            )
        else:
            self.quiet += 1
            self.pans.append(None)
        if (
            len(self.frames) * 0.02 < self.settings.segment_seconds
            and self.quiet * 0.02 < self.settings.silence_seconds
        ):
            return None
        result = None
        if self.voiced >= 10:
            result = Segment(b"".join(self.frames), self.start, self.pans)
        self.frames = []
        self.pans = []
        self.voiced = self.quiet = 0
        return result
