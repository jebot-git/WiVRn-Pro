#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Executable transport model, not a network service or WiVRn protocol.

Deadlines must ALREADY be expressed in the receiver's monotonic clock domain.
One incomplete stereo frame maximum. Independent sessions need new receivers.
CRC detects corruption; it provides no authentication.
"""
from dataclasses import dataclass, field
import struct
import zlib

HEADER = struct.Struct("<4sBBHQQHHI")  # 32 bytes
PAYLOAD = 1200 - HEADER.size
MAX_FRAGMENTS = 4096


def packetize(data: bytes, frame: int, eye: int, deadline_us: int):
    count = (len(data) + PAYLOAD - 1) // PAYLOAD
    if eye not in (0, 1) or not 1 <= count <= MAX_FRAGMENTS:
        raise ValueError("invalid eye or payload size")
    for index in range(count):
        chunk = data[index * PAYLOAD:(index + 1) * PAYLOAD]
        yield HEADER.pack(b"V444", 1, eye, 0, frame, deadline_us,
                          count, index, zlib.crc32(chunk)) + chunk


@dataclass
class Assembly:
    count: int
    chunks: dict = field(default_factory=dict)


class Receiver:
    def __init__(self):
        self.newest = -1
        self.deadline = 0
        self.eyes = {}
        self.closed = True

    def expire(self, now_us):
        if now_us >= self.deadline:
            self.eyes.clear()
            self.closed = True

    def push(self, packet: bytes, now_us: int):
        self.expire(now_us)
        if not HEADER.size < len(packet) <= 1200:
            return False
        magic, version, eye, flags, frame, deadline, count, index, crc = HEADER.unpack_from(packet)
        chunk = packet[HEADER.size:]
        if (magic != b"V444" or version != 1 or eye not in (0, 1) or flags or
                not 1 <= count <= MAX_FRAGMENTS or index >= count or
                (index < count - 1 and len(chunk) != PAYLOAD) or
                zlib.crc32(chunk) != crc or deadline <= now_us):
            return False
        if frame < self.newest:
            return False
        if frame > self.newest:
            self.newest, self.deadline = frame, deadline
            self.eyes, self.closed = {}, False
        if self.closed or self.deadline != deadline:
            return False
        assembly = self.eyes.setdefault(eye, Assembly(count))
        if assembly.count != count:
            return False
        previous = assembly.chunks.get(index)
        if previous is not None and previous != chunk:
            return False
        assembly.chunks[index] = chunk
        return True

    def take_stereo(self, now_us: int):
        self.expire(now_us)
        if self.closed or len(self.eyes) != 2:
            return None
        if any(len(a.chunks) != a.count for a in self.eyes.values()):
            return None
        result = tuple(b"".join(self.eyes[e].chunks[i] for i in range(self.eyes[e].count)) for e in (0, 1))
        self.eyes.clear()
        self.closed = True
        return self.newest, result


if __name__ == "__main__":
    print("video_mbps,stereo_frame_kB,serialization_at_300Mbps_ms,serialization_at_600Mbps_ms")
    for mbps in (50, 100, 150):
        frame_bits = mbps * 1_000_000 / 90
        print(f"{mbps},{frame_bits/8000:.3f},{frame_bits/300000:.3f},{frame_bits/600000:.3f}")
