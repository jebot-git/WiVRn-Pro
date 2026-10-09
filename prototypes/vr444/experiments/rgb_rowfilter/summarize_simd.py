#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Matched scalar/SIMD wire and whole-pipeline host comparison."""
import argparse
import json
import math
from pathlib import Path
from statistics import mean


def summarize(data):
    if data.get("pass") is not True or data.get("scalar_simd_comparison") is not True:
        raise ValueError("not a passing scalar/SIMD comparison")
    names = ["vr444_plain_lz4"] + [backend+"_rgb_"+policy+"_lz4" for backend in ("scalar", "simd") for policy in ("plain", "up", "adaptive")]
    cases = data["cases"]
    if len({c["name"] for c in cases}) != len(cases):
        raise ValueError("duplicate case")
    metrics = ["server_transcode_ms", "source_reconstruction_ms", "wrapper_encode_ms", "client_decode_ms", "server_plus_client_ms"]
    for case in cases:
        profiles = case["profiles"]
        if not case["exact_decoded_rgba"] or [p["name"] for p in profiles] != names:
            raise ValueError("profile/exactness mismatch")
        for i in range(1, 4):
            if any(profiles[i][k] != profiles[i+3][k] for k in ("wire_bytes", "selected_mode")):
                raise ValueError("scalar/SIMD representation mismatch")
        for p in profiles:
            for metric in metrics:
                samples = p[metric]["samples"]
                if len(samples) != data["repetitions"] or any(not math.isfinite(x) or x < 0 for x in samples):
                    raise ValueError("invalid samples")
    groups = {}
    for name in ["paired-a", "paired-b", "lossless", "synthetic-q1", "synthetic-q8", "all"]:
        if name.startswith("synthetic-"):
            rows = [c for c in cases if c["name"].startswith("synthetic/") and c["q"] == int(name[-1])]
        else:
            rows = [c for c in cases if name == "all" or c["name"].startswith(name+"/")]
        if not rows:
            continue
        baseline = sum(c["profiles"][0]["wire_bytes"] for c in rows)
        result = []
        for i, profile in enumerate(names):
            selected = [c["profiles"][i] for c in rows]
            total = sum(p["wire_bytes"] for p in selected)
            result.append(dict(profile=profile, wire_bytes=total, saved_percent_vs_vr444=100*(baseline-total)/baseline,
                               mean_case_p50_ms={m: mean(p[m]["p50"] for p in selected) for m in metrics}))
        delta = {policy: {m: result[i]["mean_case_p50_ms"][m]-result[i+3]["mean_case_p50_ms"][m] for m in metrics}
                 for i, policy in enumerate(("plain", "up", "adaptive"), 1)}
        groups[name] = dict(cases=len(rows), profiles=result, scalar_minus_simd_mean_case_p50_ms=delta)
    return dict(schema=1, domain=data["domain"], backend=data["simd_backend"], groups=groups,
                statistic="means of per-eye case p50, not pooled percentiles or stereo completion predictions",
                wire_contract="scalar and SIMD encoders are byte-for-byte identical in the measured comparison",
                limitations="one host without clock/thermal/affinity control; ARM64 compile-only; no headset/transfer/compositor/optical timing")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(json.loads(args.input.read_text())), indent=2)+"\n")
