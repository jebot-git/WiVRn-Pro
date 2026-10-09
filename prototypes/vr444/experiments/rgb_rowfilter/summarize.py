#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Joint wire and host encode/decode summaries, preserving Q1 and Q8 groups."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import mean


def summarize(data):
    if data.get("schema") != 1 or data.get("pass") is not True:
        raise ValueError("failed/unknown benchmark")
    cases = data["cases"]
    if len({c["name"] for c in cases}) != len(cases):
        raise ValueError("duplicate case")
    profiles = ["vr444_plain_lz4", "rgb_plain_lz4", "rgb_up_lz4", "rgb_adaptive_lz4"]
    metrics = ["server_transcode_ms", "source_reconstruction_ms", "wrapper_encode_ms", "client_decode_ms", "server_plus_client_ms"]
    for c in cases:
        if not c["exact_decoded_rgba"] or [p["name"] for p in c["profiles"]] != profiles:
            raise ValueError("exactness/profile mismatch")
        for p in c["profiles"]:
            for metric in metrics:
                samples = p[metric]["samples"]
                if len(samples) != data["repetitions"] or any(not math.isfinite(x) or x < 0 for x in samples):
                    raise ValueError("invalid samples")
    groups = {}
    for name in ["paired-a", "paired-b", "lossless", "synthetic-q1", "synthetic-q8", "all"]:
        if name.startswith("synthetic-"):
            group = [c for c in cases if c["name"].startswith("synthetic/") and c["q"] == int(name.removeprefix("synthetic-q"))]
        else:
            group = [c for c in cases if name == "all" or c["name"].startswith(name+"/")]
        if not group:
            continue
        baseline = sum(c["profiles"][0]["wire_bytes"] for c in group)
        rows = []
        for i, profile in enumerate(profiles):
            chosen = [c["profiles"][i] for c in group]
            total = sum(p["wire_bytes"] for p in chosen)
            rows.append(dict(profile=profile, wire_bytes=total, saved_bytes_vs_baseline=baseline-total,
                             saved_percent_vs_baseline=100*(baseline-total)/baseline,
                             selected_modes=dict(Counter(str(p["selected_mode"]) for p in chosen)),
                             mean_case_p50_ms={m: mean(p[m]["p50"] for p in chosen) for m in metrics}))
        groups[name] = dict(cases=len(group), profiles=rows)
    return dict(schema=1, domain="offline host only", exact_decoded_rgba=True,
                statistic="means of per-eye case p50, not pooled percentiles or stereo completion predictions",
                source_contract="Q8 preserves existing already-quantized decoded RGB; Q1 preserves existing decoded RGB",
                groups=groups, limitations="one uncontrolled-clock host, small static captured scenes and synthetics; no headset/transfer/compositor/optical timing")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(json.loads(args.input.read_text())), indent=2)+"\n")
