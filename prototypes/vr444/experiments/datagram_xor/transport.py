#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline VXF1 stereo datagram + XOR reference. Not authenticated transport.

Shares only the tested receiver ticket/epoch/lifetime primitives with VRP2;
the VXF1 framing is explicitly different and cannot be sent to its decoder.
"""
from dataclasses import dataclass, field
import importlib.util
import math
from pathlib import Path
import struct
import sys
import time
import zlib

_spec = importlib.util.spec_from_file_location(
    "vrp2_lifetime", Path(__file__).resolve().parents[1] / "datagram_v2/transport.py")
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)

HEADER = struct.Struct("<4sBBHIIQQHHHHI")
MANIFEST_BODY = struct.Struct("<16sQIIIIBBH")
MANIFEST, DATA, PARITY = 1, 2, 3
SHARD_BYTES = 1152
UDP_BUDGET = 1200
MAX_PAIR = base.MAX_PAIR
MAX_SHARDS = math.ceil(MAX_PAIR / SHARD_BYTES)
MAX_GROUPS = math.ceil(MAX_SHARDS / 4)
MAX_CANDIDATES = base.MAX_CANDIDATES
MAX_ORPHAN = base.MAX_ORPHAN
MAX_BUFFERED = 2 * (MAX_PAIR + MAX_GROUPS * SHARD_BYTES + MANIFEST_BODY.size)
assert HEADER.size == 44 and HEADER.size + SHARD_BYTES <= UDP_BUDGET


def crc(data):
    return zlib.crc32(data)


def packet(kind, session_epoch, config_epoch, frame, warp, index, count, body):
    prefix = HEADER.pack(b"VXF1", 1, kind, 0, session_epoch, config_epoch,
                         frame, warp, 0, index, count, len(body), 0)
    checksum = crc(prefix[:40] + body)
    return prefix[:40] + struct.pack("<I", checksum) + body


def packetize(session, session_epoch, config_epoch, frame, warp, deadline_ns,
              left, right, stripe_width=8):
    if len(session) != 16 or not left or not right or len(left) + len(right) > MAX_PAIR:
        raise ValueError("stereo input bounds")
    if not (0 < session_epoch < 2**32 and 0 < config_epoch < 2**32 and
            0 < frame < 2**64 and 0 < warp < 2**64 and 0 < deadline_ns < 2**64):
        raise ValueError("identity/deadline bounds")
    if stripe_width not in (0, 4, 8, 16):
        raise ValueError("stripe width")
    source = left + right
    n = math.ceil(len(source) / SHARD_BYTES)
    body = MANIFEST_BODY.pack(session, deadline_ns, len(left), len(right),
                              crc(left), crc(right), stripe_width, 0, n)
    manifest = packet(MANIFEST, session_epoch, config_epoch, frame, warp, 0, 1, body)
    shards = [source[at:at + SHARD_BYTES] for at in range(0, len(source), SHARD_BYTES)]
    packets = [manifest]
    width = stripe_width or n
    groups = math.ceil(n / width)
    for group in range(groups):
        parity = 0
        for index in range(group * width, min((group + 1) * width, n)):
            shard = shards[index]
            packets.append(packet(DATA, session_epoch, config_epoch, frame, warp, index, n, shard))
            # Repeat after one source record, before a lost first manifest could
            # accumulate more than the bounded orphan budget on a large pair.
            if index == 0:
                packets.append(manifest)
            if stripe_width:
                parity ^= int.from_bytes(shard, "little")
        if stripe_width:
            packets.append(packet(PARITY, session_epoch, config_epoch, frame, warp,
                                  group, groups, parity.to_bytes(SHARD_BYTES, "little")))
    assert all(len(p) <= UDP_BUDGET for p in packets)
    return packets


@dataclass
class Candidate:
    frame: int
    warp: int
    arrived: int
    manifest: bytes | None = None
    deadline: int | None = None
    # left bytes, right bytes, left CRC, right CRC, stripe width, source count
    info: tuple | None = None
    data: dict = field(default_factory=dict)
    parity: dict = field(default_factory=dict)

    @property
    def bytes(self):
        return sum(len(v[1]) for v in self.data.values()) + sum(len(v[1]) for v in self.parity.values()) + (len(self.manifest) if self.manifest else 0)


class Receiver(base.Receiver):
    def __init__(self, session, session_epoch=1, config_epoch=1, clock=time.monotonic_ns):
        super().__init__(session, session_epoch, config_epoch, clock)

    def _manifest(self, body, now, frame, warp):
        if len(body) != MANIFEST_BODY.size:
            raise ValueError("manifest_size")
        sid, deadline, left, right, left_crc, right_crc, width, reserved, n = MANIFEST_BODY.unpack(body)
        if sid != self.session:
            raise ValueError("session_uuid")
        if not (left > 0 and right > 0 and left + right <= MAX_PAIR):
            raise ValueError("manifest_bounds")
        if width not in (0, 4, 8, 16) or reserved or n != math.ceil((left + right) / SHARD_BYTES):
            raise ValueError("manifest_geometry")
        if not now < deadline <= now + base.MAX_FUTURE_NS:
            raise ValueError("deadline")
        if self.tickets.get((frame, warp)) != deadline:
            raise ValueError("unissued_deadline")
        return deadline, (left, right, left_crc, right_crc, width, n)

    @staticmethod
    def _valid_fragment(c, kind, index, value):
        count, body = value
        left, right, _, _, width, n = c.info
        if kind == DATA:
            return count == n and 0 <= index < n and len(body) == min(SHARD_BYTES, left + right - index * SHARD_BYTES)
        groups = math.ceil(n / width) if width else 0
        return count == groups and 0 <= index < groups and len(body) == SHARD_BYTES

    def _recover(self, c):
        left, right, _, _, width, n = c.info
        if not width:
            return
        for group, (_, parity) in c.parity.items():
            indices = range(group * width, min((group + 1) * width, n))
            missing = [i for i in indices if i not in c.data]
            if len(missing) != 1:
                continue
            index = missing[0]
            recovered = int.from_bytes(parity, "little")
            for i in indices:
                if i != index:
                    recovered ^= int.from_bytes(c.data[i][1], "little")
            length = min(SHARD_BYTES, left + right - index * SHARD_BYTES)
            padded = recovered.to_bytes(SHARD_BYTES, "little")
            # Nonzero padded tail is not part of any canonical source packet.
            if any(padded[length:]):
                return False
            c.data[index] = (n, padded[:length])
            self.stats["recovered_source_shards"] += 1
        return True

    def receive(self, incoming, now):
        self.expire(now)
        self.stats["received_datagrams"] += 1
        self.stats["received_udp_payload_bytes"] += len(incoming)
        if not HEADER.size < len(incoming) <= UDP_BUDGET:
            self.event("packet_size", now)
            return None
        magic, version, kind, flags, se, ce, frame, warp, reserved, index, count, size, checksum = HEADER.unpack_from(incoming)
        body = incoming[HEADER.size:]
        if magic != b"VXF1" or version != 1 or kind not in (MANIFEST, DATA, PARITY) or flags or reserved or size != len(body) or not frame or not warp:
            self.event("header", now, frame)
            return None
        if crc(incoming[:40] + body) != checksum:
            self.event("packet_crc", now, frame)
            return None
        if se != self.session_epoch or ce != self.config_epoch:
            self.event("epoch", now, frame)
            return None
        if frame in self.retired or (self.published and frame <= self.published.frame):
            self.event("stale_frame", now, frame)
            return None
        if (frame, warp) not in self.tickets:
            self.event("unissued_frame", now, frame)
            return None
        parsed = None
        if kind == MANIFEST:
            if (index, count) != (0, 1):
                self.event("manifest_header", now, frame)
                return None
            try:
                parsed = self._manifest(body, now, frame, warp)
            except ValueError as exc:
                self.event(str(exc), now, frame)
                return None
        elif kind == DATA:
            if not (0 <= index < count <= MAX_SHARDS and 0 < len(body) <= SHARD_BYTES):
                self.event("data_header", now, frame)
                return None
        elif not (0 <= index < count <= MAX_GROUPS and len(body) == SHARD_BYTES):
            self.event("parity_header", now, frame)
            return None
        c = self.pending.get(frame)
        if c and c.warp != warp:
            self.event("warp_conflict", now, frame)
            return None
        if c is None:
            if len(self.pending) >= MAX_CANDIDATES:
                oldest = min(self.pending)
                if frame <= oldest:
                    self.event("candidate_cap", now, frame)
                    return None
                self.retire(oldest, "superseded_incomplete", now)
            c = Candidate(frame, warp, now)
            self.pending[frame] = c
        if kind == MANIFEST:
            if c.manifest is not None:
                self.event("duplicate_manifest" if c.manifest == body else "manifest_conflict", now, frame)
                return None
            c.manifest = body
            c.deadline, c.info = parsed
            if any(not self._valid_fragment(c, t, i, v) for t, fragments in ((DATA, c.data), (PARITY, c.parity)) for i, v in fragments.items()):
                self.retire(frame, "orphan_manifest_conflict", now)
                return None
        else:
            fragments = c.data if kind == DATA else c.parity
            value = (count, body)
            if index in fragments:
                self.event("duplicate" if fragments[index] == value else "fragment_conflict", now, frame)
                return None
            if c.manifest is not None and not self._valid_fragment(c, kind, index, value):
                self.event("fragment_geometry", now, frame)
                return None
            if c.manifest is None and self.orphan_bytes + len(body) > MAX_ORPHAN:
                self.retire(frame, "orphan_cap", now)
                return None
            fragments[index] = value
        if self.buffered > MAX_BUFFERED:
            self.retire(frame, "buffer_cap", now)
            return None
        if c.manifest is not None and self._recover(c) is False:
            self.retire(frame, "parity_padding", now)
            return None
        if self.buffered > MAX_BUFFERED:
            self.retire(frame, "buffer_cap", now)
            return None
        self.highwater_bytes = max(self.highwater_bytes, self.buffered)
        self.highwater_candidates = max(self.highwater_candidates, len(self.pending))
        if c.manifest is None or len(c.data) != c.info[5]:
            return None
        left_size, right_size, left_crc, right_crc, _, n = c.info
        source = b"".join(c.data[i][1] for i in range(n))
        left, right = source[:left_size], source[left_size:]
        if len(right) != right_size or crc(left) != left_crc or crc(right) != right_crc:
            self.retire(frame, "stereo_crc", now)
            return None
        fresh = self.clock()
        if fresh >= c.deadline:
            self.retire(frame, "assembly_expired", fresh)
            return None
        result = base.Pair(frame, warp, se, ce, left, right, fresh, c.deadline)
        self.published = result
        for old in list(self.pending):
            if old <= frame:
                self.retire(old, "published" if old == frame else "superseded_complete", fresh)
        self.tickets = {key: deadline for key, deadline in self.tickets.items() if key[0] > frame}
        return result


def accounting(packets):
    counts = {"manifest": 0, "data": 0, "parity": 0}
    for p in packets:
        counts[{MANIFEST: "manifest", DATA: "data", PARITY: "parity"}[HEADER.unpack_from(p)[2]]] += 1
    return {"packets": len(packets), "kinds": counts, "udp_payload_bytes": sum(map(len, packets)),
            "assumed_ipv4_bytes": sum(map(len, packets)) + 28 * len(packets),
            "assumed_ipv6_bytes": sum(map(len, packets)) + 48 * len(packets),
            "maximum_udp_payload": max(map(len, packets))}
