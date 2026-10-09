#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Matched host-only aggregates; never infer headset or optical latency."""
import argparse
import collections
import json
import pathlib
import statistics


def summarize(data):
    if data.get("schema") != 1 or data.get("pass") is not True:
        raise ValueError("failed/unknown benchmark")
    groups = collections.defaultdict(list)
    for case in data["cases"]:
        if case.get("exact") is not True:
            raise ValueError("non-exact output")
        groups[case["name"].split("/")[0]].append(case)
    out = {"schema": 1, "domain": "offline host only", "cases": len(data["cases"]),
           "aggregation": "byte-weighted wire sizes; arithmetic mean of per-case p50 timings, not pooled p50/p95",
           "limitations": ["single host, no CPU affinity/thermal control", "no headset/network/upload/compositor measurement",
                           "captures are a small prior scene, not representative games", "Q8 preserves already-quantized decoded bytes"],
           "groups": {}}
    for name, cases in sorted(groups.items()):
        names = [p["name"] for p in cases[0]["profiles"]]
        if any([p["name"] for p in c["profiles"]] != names for c in cases):
            raise ValueError("mismatched profiles")
        baseline_bytes = sum(c["profiles"][0]["wire_bytes"] for c in cases)
        group = {"cases": len(cases), "profiles": {}}
        for i, profile in enumerate(names):
            rows = [c["profiles"][i] for c in cases]
            size = sum(r["wire_bytes"] for r in rows)
            result = {"total_wire_bytes": size, "wire_ratio_to_vr444_lz4": size / baseline_bytes}
            for metric in ("post_vr444_server_ms", "client_decode_ms", "post_vr444_combined_ms"):
                result["mean_case_p50_" + metric] = statistics.mean(r[metric]["p50"] for r in rows)
            result["cases_with_lower_client_p50_than_baseline"] = sum(
                c["profiles"][i]["client_decode_ms"]["p50"] < c["profiles"][0]["client_decode_ms"]["p50"] for c in cases)
            group["profiles"][profile] = result
        out["groups"][name] = group
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(json.loads(args.input.read_text())), indent=2) + "\n")
