#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Match same-wire, same-burst loopback profiles including offload grouping."""
import argparse
import json
import math
from pathlib import Path


def summarize(data):
    if data.get("schema") != 1 or data.get("pass") is not True:
        raise ValueError("failed/unknown measurement")
    supported = {c["profile"] for c in data["capabilities"] if c["supported"]}
    if "sendmmsg_recvmmsg" not in supported:
        raise ValueError("no baseline")
    groups = {}
    for row in data["rows"]:
        if row.get("exact") is not True:
            raise ValueError("nonexact row")
        samples = row["whole_loopback_including_plan_ms"]["samples"]
        if len(samples) != data["repetitions"] or any(not math.isfinite(x) or x <= 0 for x in samples):
            raise ValueError("invalid samples")
        key = row["packet_count"], row["nominal_packet_bytes"], row["mixed_sizes"], row["burst"]
        if row["profile"] in groups.setdefault(key, {}):
            raise ValueError("duplicate profile")
        groups[key][row["profile"]] = row
    pairs = []
    for key, profiles in sorted(groups.items()):
        if set(profiles) != supported:
            raise ValueError("unmatched profiles")
        baseline = profiles["sendmmsg_recvmmsg"]
        a = baseline["whole_loopback_including_plan_ms"]["p50"]
        for name, row in sorted(profiles.items()):
            if name == "sendmmsg_recvmmsg":
                continue
            if row["wire_bytes_per_iteration"] != baseline["wire_bytes_per_iteration"]:
                raise ValueError("wire mismatch")
            b = row["whole_loopback_including_plan_ms"]["p50"]
            pairs.append(dict(packet_count=key[0], nominal_packet_bytes=key[1], mixed_sizes=key[2], burst=key[3],
                              profile=name, baseline_p50_ms=a, candidate_p50_ms=b, saved_p50_ms=a-b,
                              candidate_to_baseline_ratio=b/a,
                              included_planning_p50_ms=row["included_group_planning_ms"]["p50"],
                              send_calls_per_iteration=row["send_calls"]/data["repetitions"],
                              receive_calls_per_iteration=row["receive_calls"]/data["repetitions"],
                              max_segments_per_receive=row["max_segments_per_receive"],
                              would_block=row["would_block"], polls=row["poll_calls"]))
    return dict(schema=1, domain=data["domain"], matched_cases=len(groups), pairs=pairs,
                limitations="one host, serialized endpoints, no controlled clocks/thermal state; Linux software loopback offload is not NIC/WiFi/headset evidence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(json.loads(args.input.read_text())), indent=2)+"\n")
