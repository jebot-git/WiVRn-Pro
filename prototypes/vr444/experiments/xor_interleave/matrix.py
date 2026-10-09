#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Same-overhead contiguous/interleaved FEC under bounded deterministic erasures."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
from transport import *

SID = b"interleave-model"
NOW = 1_000_000_000


def run(size, width, layout, rate_mbps, scenario, burst=0, position="first"):
    module = xor if layout == "contiguous" else sys.modules[Receiver.__module__]
    rng = random.Random(5109 + size)
    left = rng.randbytes(size//3)
    right = rng.randbytes(size-len(left))
    clock = [NOW]
    receiver = module.Receiver(SID, clock=lambda: clock[0])
    deadline = receiver.issue_deadline(NOW, 50_000_000, 1, 10)
    packets = module.packetize(SID, 1, 1, 1, 10, deadline, left, right, width)
    source_count = math.ceil(size/SHARD_BYTES)
    groups = math.ceil(source_count/width)
    population = source_count if scenario == "source_burst" else len(packets)
    length = min(population, burst)
    start = {"first": 0, "middle": max(0, population//2-length//2), "last": population-length}[position]
    erased = set(range(start, start+length))
    events, dropped, served = [], [], NOW
    for ordinal, packet in enumerate(packets):
        fields = HEADER.unpack_from(packet)
        kind, index = fields[2], fields[9]
        # Even erased datagrams consumed the simulated serialized link service.
        served += ((len(packet)+28)*8*1000 + rate_mbps-1)//rate_mbps
        drop = (scenario == "source_burst" and kind == DATA and index in erased or
                scenario == "wire_burst" and ordinal in erased or
                scenario == "periodic_same_interleave_group" and kind == DATA and index in (0, groups) or
                scenario == "late_parity" and kind == DATA and index == 0)
        if drop:
            dropped.append(dict(ordinal=ordinal, kind=kind, index=index))
            continue
        arrival = served + (40_000_000 if scenario == "late_parity" and kind == PARITY else 0)
        events.append((arrival, ordinal, packet))
    published = []
    for arrival, _, packet in sorted(events):
        clock[0] = arrival
        result = receiver.receive(packet, arrival)
        if result:
            assert (result.left, result.right) == (left, right)
            assert result.published_receiver_ns < deadline
            published.append(result)
        assert len(receiver.pending) <= MAX_CANDIDATES
        assert receiver.buffered <= MAX_BUFFERED and receiver.orphan_bytes <= MAX_ORPHAN
    assert len(published) <= 1
    if scenario == "clean" and served < deadline:
        assert len(published) == 1
    if (layout == "interleaved" and scenario == "source_burst" and length <= groups
            and served < deadline):
        assert len(published) == 1
    if scenario == "periodic_same_interleave_group" and layout == "interleaved" and groups < source_count:
        assert not published
    first = min((e[0] for e in events), default=None)
    complete = published[0].published_receiver_ns if published else None
    return dict(pair_bytes=size, stripe_width=width, source_shards=source_count, parity_groups=groups,
                layout=layout, service_rate_mbps=rate_mbps, scenario=scenario, burst=burst,
                position=position, actual_erased_records=len(dropped), erased=dropped,
                published=len(published), exact=True,
                modeled_first_record_to_stereo_ms=(complete-first)/1e6 if complete is not None else None,
                modeled_generation_to_stereo_ms=(complete-NOW)/1e6 if complete is not None else None,
                scheduled_all_wire_service_ms=(served-NOW)/1e6,
                recovered_shards=receiver.stats['recovered_source_shards'],
                receiver_highwater_bytes=receiver.highwater_bytes,
                source_sha256=hashlib.sha256(left+right).hexdigest(),
                wire_sha256=hashlib.sha256(b''.join(packets)).hexdigest(),
                accounting=accounting(packets), counters=dict(receiver.stats))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    cases = []
    for size in (2305, 32768, 65536, MAX_PAIR):
        for width in (4, 8, 16):
            for rate in (50, 100, 300):
                jobs = [(scenario, burst, position) for scenario in ("source_burst", "wire_burst")
                        for burst in (1, 2, 4, 8, 16, 32) for position in ("first", "middle", "last")]
                jobs += [(scenario, 0, "first") for scenario in ("clean", "periodic_same_interleave_group", "late_parity")]
                for scenario, burst, position in jobs:
                    pair = [run(size, width, layout, rate, scenario, burst, position)
                            for layout in ("contiguous", "interleaved")]
                    assert pair[0]['source_sha256'] == pair[1]['source_sha256']
                    assert pair[0]['accounting'] == pair[1]['accounting']
                    cases.extend(pair)
    counts = Counter()
    for a, b in zip(cases[::2], cases[1::2]):
        counts[f"{a['scenario']}:contiguous={a['published']},interleaved={b['published']}"] += 1
    result = dict(schema=1, pass_result=True, no_socket=True, no_headset=True,
                  cases=len(cases), matched_comparisons=len(cases)//2,
                  model="IPv4 serialized service at50/100/300Mbps; dropped records consume service;50ms receiver ticket; optional40ms parity delay",
                  limitation="deterministic synthetic erasures; no WiFi/MAC/congestion/ACK/jitter/CPU duration; completion counts are not network reliability rates or headset latency",
                  summary=dict(counts), rows=cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({key: result[key] for key in ('pass_result','cases','matched_comparisons','summary')}))


if __name__ == '__main__':
    main()
