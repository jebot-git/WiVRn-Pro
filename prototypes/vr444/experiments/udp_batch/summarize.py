#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pair exact localhost syscall cases without extrapolating to headset latency."""
import argparse
import json
from pathlib import Path


def summarize(data):
    if data.get("schema") != 1 or data.get("pass") is not True:
        raise ValueError("failed/unknown measurement")
    groups = {}
    for row in data["rows"]:
        if row.get("exact") is not True:
            raise ValueError("nonexact row")
        key = (row["packet_count"], row["packet_bytes"], row["burst"])
        if row["batched"] in groups.setdefault(key, {}):
            raise ValueError("duplicate profile")
        groups[key][row["batched"]] = row
    pairs = []
    reps = data["repetitions"]
    for key, profiles in sorted(groups.items()):
        if set(profiles) != {False, True}:
            raise ValueError("unmatched case")
        a, b = profiles[False], profiles[True]
        one, many = a["whole_loopback_iteration_ms"]["p50"], b["whole_loopback_iteration_ms"]["p50"]
        pairs.append(dict(packet_count=key[0], packet_bytes=key[1], burst=key[2],
                          single_call_p50_ms=one, batch_p50_ms=many,
                          batch_to_single_p50_ratio=many / one, saved_p50_ms=one - many,
                          send_calls_per_iteration=b["send_calls"] / reps,
                          receive_calls_per_iteration=b["receive_calls"] / reps,
                          natural_would_block=b["would_block"], polls=b["poll_calls"]))
    return dict(schema=1, domain=data["domain"], paired_cases=len(pairs), rows=pairs,
                limitations="single host; serialized sender/receiver; background apps and clocks uncontrolled; no WiFi/headset/CPU-decode/optical prediction")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(json.loads(args.input.read_text())), indent=2) + "\n")
