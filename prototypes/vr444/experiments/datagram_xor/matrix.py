#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Deterministic FEC correctness/overhead matrix, not a network benchmark."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
from transport import *

SID = b"matrix-session!!"
NOW = 1_000_000_000
SCENARIOS = ("clean", "lose_first_manifest", "lose_both_manifests", "lose_first_source",
             "lose_one_per_stripe", "lose_two_first_stripe", "lose_all_parity", "corrupt_first_source",
             "reverse", "shuffle", "random_2pct", "random_10pct", "late_parity", "expired_all")


def impair(packets, scenario, width, seed):
    rng = random.Random(seed)
    selected = []
    for ordinal, p in enumerate(packets):
        fields = HEADER.unpack_from(p)
        kind, index = fields[2], fields[9]
        drop = (scenario == "lose_first_manifest" and ordinal == 0 or
                scenario == "lose_both_manifests" and kind == MANIFEST or
                scenario in ("lose_first_source", "late_parity") and kind == DATA and index == 0 or
                scenario == "lose_one_per_stripe" and kind == DATA and index % (width or MAX_SHARDS) == 0 or
                scenario == "lose_two_first_stripe" and kind == DATA and index in (0, 1) or
                scenario == "lose_all_parity" and kind == PARITY or
                scenario == "random_2pct" and rng.random() < .02 or
                scenario == "random_10pct" and rng.random() < .1)
        if drop:
            continue
        if scenario == "corrupt_first_source" and kind == DATA and index == 0:
            p = p[:-1] + bytes([p[-1] ^ 1])
        selected.append(p)
    if scenario == "reverse":
        selected.reverse()
    if scenario == "shuffle":
        rng.shuffle(selected)
    events = []
    for i, p in enumerate(selected):
        arrival = NOW + (i + 1) * 10_000
        if scenario == "late_parity" and HEADER.unpack_from(p)[2] == PARITY:
            arrival += 60_000_000
        if scenario == "expired_all":
            arrival += 60_000_000
        events.append((arrival, i, p))
    return sorted(events), len(packets) - len(selected)


def run(size, width, seed, scenario):
    rng = random.Random(seed + size)
    left = rng.randbytes(size // 3)
    right = rng.randbytes(size - len(left))
    clock = [NOW]
    receiver = Receiver(SID, clock=lambda: clock[0])
    deadline = receiver.issue_deadline(NOW, 50_000_000, 1, 10)
    packets = packetize(SID, 1, 1, 1, 10, deadline, left, right, width)
    events, dropped = impair(packets, scenario, width, seed)
    published = []
    for arrival, _, p in events:
        clock[0] = arrival
        out = receiver.receive(p, arrival)
        if out:
            assert (out.left, out.right) == (left, right)
            assert out.frame == 1 and out.warp == 10 and out.published_receiver_ns < deadline
            published.append(out)
        assert len(receiver.pending) <= MAX_CANDIDATES
        assert receiver.buffered <= MAX_BUFFERED and receiver.orphan_bytes <= MAX_ORPHAN
    assert len(published) <= 1
    if scenario in ("clean", "lose_first_manifest", "lose_all_parity"):
        assert len(published) == 1
    if scenario in ("lose_first_source", "corrupt_first_source", "lose_one_per_stripe") and width:
        assert len(published) == 1
    if scenario in ("lose_both_manifests", "late_parity", "expired_all"):
        assert not published
    return dict(pair_bytes=size, stripe_width=width, seed=seed, scenario=scenario,
                published=len(published), exact=True, dropped_packets=dropped,
                recovered_shards=receiver.stats["recovered_source_shards"],
                buffer_highwater=receiver.highwater_bytes,
                source_sha256=hashlib.sha256(left + right).hexdigest(),
                wire_sha256=hashlib.sha256(b"".join(packets)).hexdigest(),
                accounting=accounting(packets), counters=dict(receiver.stats))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = [run(size, width, seed, scenario)
            for size in (3, 2304, 2305, 32768, 65536, MAX_PAIR)
            for width in (0, 4, 8, 16) for seed in (1, 22, 333) for scenario in SCENARIOS]
    totals = Counter()
    for row in rows:
        totals[f"width_{row['stripe_width']}_published"] += row["published"]
        totals[f"width_{row['stripe_width']}_cases"] += 1
    result = dict(schema=1, pass_=True, domain="deterministic in-process packet-list transformations",
                  no_socket=True, no_headset=True, cases=len(rows),
                  synthetic_clock="10 microseconds per transformed record; 50ms receiver ticket; selected 60ms delays; no processing duration measured",
                  conclusions="correctness and exact bytes/overhead only; publication counts are specific to synthetic cases, not predicted network reliability",
                  summary=dict(totals), rows=rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("pass_", "cases", "summary")}))


if __name__ == "__main__":
    main()
