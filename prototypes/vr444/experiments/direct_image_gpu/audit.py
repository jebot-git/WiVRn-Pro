#!/usr/bin/env python3
"""Fail-closed correctness audit; timing fields are not promotion evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path

MODES = ("buffer_copy", "image_unorm", "image_uint")
TIMES = ("validation_ms", "input_copy_ms", "stereo_gpu_ms", "pipeline_wall_ms", "oracle_ms")


def audit(data):
    def need(ok, message):
        if not ok:
            raise ValueError(message)

    need(data.get("schema") == 1, "schema")
    need(data.get("experiment") == "direct-image-gpu", "experiment")
    need(data.get("headset_used") is False, "headset scope")
    need(data.get("pass") is True and data.get("promotion_blocked") is True, "completion/promotion gate")
    need(type(data.get("format_list")) is bool, "extension mode")
    need(data.get("warmups") == 1, "warmup count")
    reps = data.get("repetitions")
    need(type(reps) is int and 1 <= reps <= 30, "repetition bound")
    cases = data.get("cases")
    need(isinstance(cases, list) and len(cases) >= 84, "fixture suite incomplete")
    need(data.get("case_count") == len(cases), "case count")
    names = set()
    devices = set()
    software = False
    maximum_linear = maximum_code = 0.0
    for index, case in enumerate(cases):
        name = case.get("name")
        need(isinstance(name, str) and name and name not in names, "case name")
        names.add(name)
        devices.add(case.get("device"))
        need(isinstance(case.get("device"), str) and case["device"], "device identity")
        need(type(case.get("device_type")) is int and 0 <= case["device_type"] <= 4, "device type")
        software |= case["device_type"] == 4
        need(case.get("input_memory_flags", 0) & 6 == 6, "coherent host input memory")
        rows = case.get("iterations")
        need(isinstance(rows, list) and len(rows) == reps * 3, "iteration count")
        for position, row in enumerate(rows):
            rep, order = divmod(position, 3)
            need(row.get("rep") == rep and row.get("order") == order, "chronological order")
            need(row.get("mode") == MODES[(index + rep + 1 + order) % 3], "counterbalanced mode")
            for key in ("bytes_exact", "one_eotf", "sampled_baseline_exact", "malformed_pair_rejected_before_submit"):
                need(row.get(key) is True, key)
            for key in TIMES + ("max_linear_error", "max_srgb_code_error"):
                value = row.get(key)
                need(type(value) in (int, float) and math.isfinite(value) and value >= 0, key)
            need(row["max_srgb_code_error"] < 0.5, "color code roundtrip")
            maximum_linear = max(maximum_linear, row["max_linear_error"])
            maximum_code = max(maximum_code, row["max_srgb_code_error"])
    required = {
        f"{w}x{h}-pattern{k}-q{q}"
        for w, h in ((1, 1), (1, 17), (17, 1), (17, 19), (31, 33), (65, 47), (256, 2))
        for k in range(6)
        for q in (1, 8)
    }
    need(required <= names, "required edge/color/dense/mixed fixtures missing")
    return {
        "correctness_pass": True,
        "case_count": len(cases),
        "measured_iterations": len(cases) * reps * 3,
        "devices": sorted(devices),
        "software": software,
        "format_list": data["format_list"],
        "max_ideal_linear_error": maximum_linear,
        "max_srgb_code_error": maximum_code,
        "performance_claim": False,
        "promotion_blocked": True,
        "headset_used": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    report = audit(json.loads(raw))
    report["result_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
