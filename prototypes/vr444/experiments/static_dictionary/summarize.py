#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggregate matched held-out cases; case p50 means are not pooled percentiles."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import mean


def summarize(bench, split):
    if not bench["pass"] or bench["dictionary_sha256"] != split["dictionary_sha256"]:
        raise ValueError("failed benchmark or dictionary mismatch")
    expected = {r["path"] for r in split["source_files"] if not r["training"]}
    cases = bench["cases"]
    if len(cases) != len(expected) or {c["name"] for c in cases} != expected:
        raise ValueError("held-out set mismatch")
    profiles = ["plain_lz4", "dictionary_fast_adaptive", "dictionary_slow_adaptive"]
    metrics = ["encode_ms", "unwrap_ms", "client_with_vr444_ms", "encode_plus_client_ms"]
    for c in cases:
        if not c["exact"] or [p["name"] for p in c["profiles"]] != profiles:
            raise ValueError("exactness/profile mismatch")
        for p in c["profiles"]:
            if p["wire_bytes"] > c["profiles"][0]["wire_bytes"]:
                raise ValueError("adaptive wire growth")
            for metric in metrics:
                samples = p[metric]["samples"]
                if len(samples) != bench["repetitions"] or any(not math.isfinite(x) or x < 0 for x in samples):
                    raise ValueError("invalid samples")
    groups = {}
    for name in ["paired-a", "paired-b", "lossless", "synthetic", "all"]:
        group = [c for c in cases if name == "all" or c["name"].startswith(name + "/")]
        baseline = sum(c["profiles"][0]["wire_bytes"] for c in group)
        rows = []
        for i, profile in enumerate(profiles):
            selected = [c["profiles"][i] for c in group]
            wire = sum(p["wire_bytes"] for p in selected)
            saving = baseline - wire
            rows.append(dict(profile=profile, wire_bytes=wire, saved_bytes_vs_plain=saving,
                             saved_percent_vs_plain=100 * saving / baseline,
                             selected_modes=dict(Counter(str(p["selected_mode"]) for p in selected)),
                             mean_case_p50_ms={m: mean(p[m]["p50"] for p in selected) for m in metrics}))
        groups[name] = dict(cases=len(group), profiles=rows)
    return dict(schema=1, domain="offline host only", pass_exact=True,
                statistic="mean of per-case p50, not a pooled latency percentile",
                dictionary_bytes=bench["dictionary_bytes"], groups=groups,
                all_once_including_dictionary_bytes={p["profile"]: p["wire_bytes"] + (bench["dictionary_bytes"] if i else 0)
                                                     for i, p in enumerate(groups["all"]["profiles"])},
                one_time_dictionary_transfer_caveat="counts dictionary bytes once; excludes provisioning framing and timing",
                recommendation="Do not promote: no held-out capture wire savings; adaptive compression adds server work")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("split", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(json.loads(args.benchmark.read_text()), json.loads(args.split.read_text())), indent=2))


if __name__ == "__main__":
    main()
