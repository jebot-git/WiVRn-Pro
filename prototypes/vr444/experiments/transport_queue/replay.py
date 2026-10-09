#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded stereo sender policy simulation with the real VXF1 receiver.

Fixed serialized service is an explicit synthetic input, not measured network
capacity. All source packet and publication correctness checks execute normally.
"""
import argparse
from collections import Counter
from dataclasses import dataclass
import importlib.util
import json
import math
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "vxf_queue_reference", Path(__file__).resolve().parents[1] / "datagram_xor/transport.py")
wire = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = wire
_spec.loader.exec_module(wire)

SID = b"queue-session!!!"
ORIGIN = 1_000_000_000
POLICIES = ("fifo_two", "latest_pending", "eager_preempt")


def pixels(frame, size):
    block = bytes((frame * 17 + i * 13) % 256 for i in range(256))
    data = (block * math.ceil(size / len(block)))[:size]
    at = size // 3
    return data[:at], data[at:]


@dataclass
class Frame:
    frame: int
    deadline: int
    packets: list
    cursor: int = 0

    @property
    def bytes(self):
        # Conservative retained storage includes sent records until frame release.
        return sum(map(len, self.packets))


class Sender:
    def __init__(self, policy):
        if policy not in POLICIES:
            raise ValueError("unknown sender policy")
        self.policy = policy
        self.current = None
        self.pending = None
        self.stats = Counter()
        self.highwater_frames = 0
        self.highwater_bytes = 0
        self.last_offered = 0

    def check_bounds(self):
        frames = [f for f in (self.current, self.pending) if f]
        assert len(frames) <= 2
        self.highwater_frames = max(self.highwater_frames, len(frames))
        self.highwater_bytes = max(self.highwater_bytes, sum(f.bytes for f in frames))

    def offer(self, frame):
        maximum_records = wire.MAX_SHARDS + wire.MAX_GROUPS + 2
        if not (self.last_offered < frame.frame < 2**64 and 0 < frame.deadline < 2**64 and
                frame.cursor == 0 and 0 < len(frame.packets) <= maximum_records and
                all(isinstance(p, bytes) and 0 < len(p) <= wire.UDP_BUDGET for p in frame.packets)):
            raise ValueError("sender frame bounds/order")
        self.last_offered = frame.frame
        self.stats["offered_frames"] += 1
        if self.current is None:
            self.current = frame
        elif self.policy == "fifo_two":
            if self.pending is None:
                self.pending = frame
            else:
                self.stats["rejected_new_frames"] += 1
        elif self.policy == "latest_pending":
            if self.pending is not None:
                self.stats["superseded_pending_frames"] += 1
            self.pending = frame
        else:
            if self.current.cursor < len(self.current.packets):
                self.stats["preempted_incomplete_frames"] += 1
                self.stats["preempted_unsent_records"] += len(self.current.packets) - self.current.cursor
            else:
                self.stats["detached_fully_dispatched_frames"] += 1
            self.current = frame
        self.check_bounds()

    def next_packet(self, now):
        while True:
            if self.current is None:
                self.current, self.pending = self.pending, None
            if self.current is None:
                return None
            if now >= self.current.deadline:
                self.stats["expired_sender_frames"] += 1
                self.current = None
                continue
            if self.current.cursor >= len(self.current.packets):
                self.current = None
                continue
            f = self.current
            p = f.packets[f.cursor]
            f.cursor += 1
            self.stats["dispatched_records"] += 1
            self.stats["dispatched_udp_payload_bytes"] += len(p)
            self.check_bounds()
            return f.frame, p

    def completed_packet(self, frame):
        if self.current and self.current.frame == frame and self.current.cursor == len(self.current.packets):
            self.stats["fully_dispatched_frames"] += 1
            self.current, self.pending = self.pending, None


def distribution(values):
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    v = sorted(values)
    return {"count": len(v), "p50": v[(len(v) - 1) // 2], "p95": v[math.ceil(.95 * len(v)) - 1], "max": v[-1]}


def simulate(policy="latest_pending", pair_bytes=32768, rate_mbps=100,
             fps=90, frames=120, ttl_ms=50, stripe_width=0):
    if not (3 <= pair_bytes <= wire.MAX_PAIR and 1 <= rate_mbps <= 1000 and
            1 <= fps <= 240 and 1 <= frames <= 1000 and 1 <= ttl_ms <= 100):
        raise ValueError("simulation bounds")
    if stripe_width not in (0, 4, 8, 16):
        raise ValueError("stripe width")
    sender = Sender(policy)
    now = [ORIGIN]
    receiver = wire.Receiver(SID, clock=lambda: now[0])
    period = round(1_000_000_000 / fps)
    offered = 0
    flight = None
    first_receive = {}
    publications = []
    source_age, assembly_time = [], []
    wire_bytes = 0

    def dispatch():
        nonlocal flight
        item = sender.next_packet(now[0])
        if item is not None:
            frame, packet = item
            # Explicit single-server serializer with fixed IPv4+UDP overhead.
            duration = math.ceil((len(packet) + 28) * 8000 / rate_mbps)
            flight = (now[0] + duration, frame, packet)

    while offered < frames or flight is not None or sender.current or sender.pending:
        next_offer = ORIGIN + offered * period if offered < frames else math.inf
        next_complete = flight[0] if flight else math.inf
        if next_offer <= next_complete:
            now[0] = next_offer
            offered += 1
            warp = offered * 7 + 1
            deadline = receiver.issue_deadline(now[0], ttl_ms * 1_000_000, offered, warp)
            left, right = pixels(offered, pair_bytes)
            packets = wire.packetize(SID, 1, 1, offered, warp, deadline, left, right, stripe_width)
            sender.offer(Frame(offered, deadline, packets))
            first_receive = {f: t for f, t in first_receive.items() if (f, f * 7 + 1) in receiver.tickets}
            if flight is None:
                dispatch()
        else:
            assert flight is not None
            now[0], frame, packet = flight
            flight = None
            wire_bytes += len(packet) + 28
            if (frame, frame * 7 + 1) in receiver.tickets:
                first_receive.setdefault(frame, now[0])
            out = receiver.receive(packet, now[0])
            if out:
                assert (out.left, out.right) == pixels(frame, pair_bytes)
                assert out.frame == frame and out.warp == frame * 7 + 1
                assert out.published_receiver_ns < out.deadline_receiver_ns
                assert not publications or out.frame > publications[-1]
                publications.append(frame)
                source_age.append((now[0] - (ORIGIN + (frame - 1) * period)) / 1e6)
                assembly_time.append((now[0] - first_receive[frame]) / 1e6)
            first_receive = {f: t for f, t in first_receive.items() if (f, f * 7 + 1) in receiver.tickets}
            sender.completed_packet(frame)
            dispatch()
        assert len(receiver.pending) <= wire.MAX_CANDIDATES
        assert receiver.buffered <= wire.MAX_BUFFERED
        assert receiver.orphan_bytes <= wire.MAX_ORPHAN
        assert len(first_receive) <= wire.base.MAX_TICKETS
        sender.check_bounds()
    elapsed = (now[0] - ORIGIN) / 1e9
    before_last_offer = sum(f < frames for f in publications)
    return dict(policy=policy, pair_bytes=pair_bytes, rate_mbps=rate_mbps, fps=fps, offered=frames,
                ttl_ms=ttl_ms, stripe_width=stripe_width, complete_pairs=len(publications),
                complete_pairs_excluding_last_frame=before_last_offer,
                published_frame_ids=publications, exact=True,
                simulated_source_age_ms=distribution(source_age),
                simulated_first_record_to_assembly_ms=distribution(assembly_time),
                simulation_elapsed_seconds=elapsed, assumed_ipv4_bytes_sent=wire_bytes,
                sender_counts=dict(sender.stats), sender_highwater_frames=sender.highwater_frames,
                sender_highwater_payload_bytes=sender.highwater_bytes,
                receiver_highwater_payload_bytes=receiver.highwater_bytes,
                receiver_counts=dict(receiver.stats))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = [simulate(policy=policy, pair_bytes=size, rate_mbps=rate, frames=90, ttl_ms=ttl, stripe_width=width)
            for size in (32768, 65536, 262144) for rate in (20, 50, 100, 300)
            for ttl in (12, 30, 50) for width in (0, 8) for policy in POLICIES]
    result = dict(schema=1, pass_=True, domain="deterministic sender/link-service simulation only",
                  model="single fixed-rate serializer; each UDP record adds28 assumed IPv4 bytes; no loss/ACK/congestion/security/CPU/propagation model",
                  policy_tie_rule="new frame offer precedes completion when timestamps tie; in-flight record is never cancelled",
                  transport="actual VXF1 packer and bounded receiver with exact complete-stereo checks",
                  frames_per_case=90, cases=len(rows), rows=rows,
                  limitations="not TCP/QUIC/WiFi/headset latency or observed bandwidth; input rate/clock/deadlines are synthetic")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"pass": True, "cases": len(rows)}))


if __name__ == "__main__":
    main()
