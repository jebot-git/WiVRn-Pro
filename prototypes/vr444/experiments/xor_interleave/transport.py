#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""VXI1 interleaved XOR groups; a separate bounded offline wire profile."""
import importlib.util
import math
from pathlib import Path
import struct
import sys

spec = importlib.util.spec_from_file_location(
    "interleave_xor_reference", Path(__file__).resolve().parents[1] / "datagram_xor/transport.py")
xor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = xor
spec.loader.exec_module(xor)

HEADER = xor.HEADER
MANIFEST_BODY = xor.MANIFEST_BODY
MANIFEST, DATA, PARITY = xor.MANIFEST, xor.DATA, xor.PARITY
SHARD_BYTES, MAX_PAIR = xor.SHARD_BYTES, xor.MAX_PAIR
MAX_SHARDS, MAX_GROUPS = xor.MAX_SHARDS, xor.MAX_GROUPS
MAX_CANDIDATES, MAX_BUFFERED, MAX_ORPHAN = xor.MAX_CANDIDATES, xor.MAX_BUFFERED, xor.MAX_ORPHAN
crc, accounting, base = xor.crc, xor.accounting, xor.base


def packet(kind, session_epoch, config_epoch, frame, warp, index, count, body):
    prefix = HEADER.pack(b"VXI1", 1, kind, 0, session_epoch, config_epoch,
                         frame, warp, 0, index, count, len(body), 0)
    return prefix[:40] + struct.pack("<I", crc(prefix[:40] + body)) + body


def group_indices(group, width, source_count):
    if width not in (4, 8, 16) or not 0 < source_count <= MAX_SHARDS:
        raise ValueError("interleaved geometry")
    groups = math.ceil(source_count / width)
    if not 0 <= group < groups:
        raise ValueError("interleaved group")
    return range(group, source_count, groups)


class Receiver(xor.Receiver):
    wire_magic = b"VXI1"
    group_indices = staticmethod(group_indices)


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
    groups = math.ceil(n / stripe_width) if stripe_width else 0
    body = MANIFEST_BODY.pack(session, deadline_ns, len(left), len(right),
                              crc(left), crc(right), stripe_width, 0, n)
    manifest = packet(MANIFEST, session_epoch, config_epoch, frame, warp, 0, 1, body)
    packets = [manifest]
    parity = [0] * groups
    last = [group_indices(g, stripe_width, n)[-1] for g in range(groups)]
    for index, at in enumerate(range(0, len(source), SHARD_BYTES)):
        shard = source[at:at + SHARD_BYTES]
        packets.append(packet(DATA, session_epoch, config_epoch, frame, warp, index, n, shard))
        if index == 0:
            packets.append(manifest)
        if stripe_width:
            group = index % groups
            parity[group] ^= int.from_bytes(shard, "little")
            # Earliest possible parity: immediately after the group's last
            # source in original raster/data order; no new cross-frame delay.
            if index == last[group]:
                packets.append(packet(PARITY, session_epoch, config_epoch, frame, warp,
                                      group, groups, parity[group].to_bytes(SHARD_BYTES, "little")))
    assert all(len(p) <= xor.UDP_BUDGET for p in packets)
    return packets
