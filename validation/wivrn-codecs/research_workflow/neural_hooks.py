#!/usr/bin/env python3
"""Manual trusted native adapter hooks; integrity/consistency, never qualification."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys

PLAN = "wivrn-neural-live-plan-v1"
CAPTURE = "wivrn-neural-device-capture-v1"
RECEIPT = "wivrn-neural-capture-validation-v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_JSON = 8 * 1024 * 1024


def need(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def pairs_unique(items):
    result = {}
    for key, value in items:
        need(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def parse(data):
    value = json.loads(data, object_pairs_hook=pairs_unique,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    json.dumps(value, allow_nan=False)  # also rejects numeric exponent overflow
    return value


def regular(path, budget, collect=False):
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode), "regular nonsymlink file required: " + str(path))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino)
             and stat.S_ISREG(opened.st_mode), "file changed while opening")
        need(opened.st_size <= budget, "file exceeds budget")
        digest, count, chunks = hashlib.sha256(), 0, []
        while True:
            chunk = stream.read(min(1024 * 1024, budget + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            need(count <= budget, "file exceeds budget")
            digest.update(chunk)
            if collect:
                chunks.append(chunk)
        need(count == opened.st_size, "file size changed while reading")
    return {"sha256": digest.hexdigest(), "bytes": count}, b"".join(chunks)


def confined(root, name):
    need(isinstance(name, str) and name and "\\" not in name, "invalid artifact path")
    part = Path(name)
    need(not part.is_absolute() and all(x not in ("..", ".") for x in part.parts),
         "artifact path escapes root")
    current = root
    need(not current.is_symlink() and current.is_dir(), "capture root changed")
    for component in part.parts:
        current = current / component
        need(not current.is_symlink(), "artifact symlink")
    return current


def json_file(path):
    record, raw = regular(path, MAX_JSON, True)
    return parse(raw), record, raw


def plan_file(path):
    path = Path(path).absolute()
    plan, record, raw = json_file(path)
    need(isinstance(plan, dict) and plan.get("schema") == PLAN, "wrong plan schema")
    need(isinstance(plan.get("run_id"), str) and plan["run_id"], "run_id required")
    need(plan.get("evidence") in ("native_headset", "synthetic_fixture"), "explicit evidence required")
    identity = plan.get("identity", {})
    blockers = []
    for name in ("candidate", "baseline", "harness"):
        pin = identity.get(name, {})
        need(isinstance(pin.get("repository"), str) and pin["repository"], name + " repository")
        need(isinstance(pin.get("ref"), str) and pin["ref"], name + " descriptive ref")
        sha = pin.get("commit")
        need(sha is None or bool(COMMIT.fullmatch(sha)), name + " immutable commit")
        if sha is None:
            blockers.append(name + " commit unbound")
    need(type(plan.get("learned")) is bool, "learned boolean required")
    inputs = plan.get("inputs", {})
    need(set(inputs) == ({"config", "dataset", "model"} if plan["learned"]
                         else {"config", "dataset"}), "exact input roles required")
    for name, value in inputs.items():
        need(isinstance(value, dict), "input record required")
        need(value.get("sha256") is None or bool(HEX.fullmatch(value["sha256"])), "input digest")
        need(value.get("path") is None or isinstance(value["path"], str), "input path")
        if not value.get("path") or value.get("sha256") is None:
            blockers.append(name + " artifact unbound")
    need(plan.get("quality", {}).get("mode") in ("lossless", "q8_exact", "new_lossy"), "quality mode")
    need(isinstance(plan["quality"].get("reference"), str) and plan["quality"]["reference"],
         "quality reference required")
    route = plan.get("route", {})
    need(all(isinstance(route.get(k), str) and route[k]
             for k in ("profile_id", "protocol", "path", "measurement_layer")), "explicit route")
    need(isinstance(plan.get("setup_declared"), dict), "declared setup required")
    need(plan.get("gaze", {}).get("arm") in
         ("none", "simulated", "replayed", "device_shared", "device_independent"), "gaze arm")
    if plan["gaze"]["arm"] != "none":
        need(plan["gaze"].get("mode") in ("shared_gaze", "independent_current_gaze",
             "independent_predicted_gaze"), "separate gaze comparison mode required")
    limits = plan.get("limits", {})
    for name in ("timeout_seconds", "max_artifact_bytes", "max_total_bytes", "max_rows"):
        need(integer(limits.get(name), 1), "positive limit: " + name)
    cap = plan.get("stereo_payload_cap_bytes")
    need(cap is None or integer(cap, 1), "payload cap")
    argv = plan.get("adapter_argv")
    if argv is not None:
        need(isinstance(argv, list) and argv and all(isinstance(t, str) and "\0" not in t
             for t in argv), "explicit argv array")
        need("{plan}" in argv and "{output}" in argv, "standalone path placeholders required")
        need("{" not in argv[0] and "}" not in argv[0], "literal executable required")
        for token in argv:
            need(token in ("{plan}", "{output}") or ("{" not in token and "}" not in token),
                 "only standalone plan/output placeholders")
    return path, plan, record, raw, blockers


def bind_inputs(plan_path, plan):
    records = {}
    for name, value in plan["inputs"].items():
        path = Path(value["path"])
        if not path.is_absolute():
            path = plan_path.parent / path
        record, _ = regular(path, plan["limits"]["max_artifact_bytes"])
        need(record["sha256"] == value["sha256"], "changed input: " + name)
        records[name] = record
    return records


def rows(root, name, maximum):
    _, raw = regular(confined(root, name), MAX_JSON, True)
    lines = raw.splitlines()
    need(len(lines) <= maximum and all(lines), "invalid/excess JSONL rows")
    return [parse(line) for line in lines]


def ident(row):
    keys = ("pair_id", "source_id", "warp_id", "target_id")
    need(all(isinstance(row.get(k), str) and row[k] for k in keys), "complete stereo identity")
    return {k: row[k] for k in keys}


def validate_capture(plan, plan_sha, root):
    capture, capture_record, _ = json_file(confined(root, "capture.json"))
    need(capture.get("schema") == CAPTURE, "device capture schema required")
    need(capture.get("run_id") == plan["run_id"] and capture.get("plan_sha256") == plan_sha,
         "capture/plan identity mismatch")
    need(capture.get("identity") == plan["identity"] and capture.get("route") == plan["route"],
         "candidate/harness/baseline/route mismatch")
    need(capture.get("evidence") == plan["evidence"], "evidence relabeling")
    need(capture.get("input_sha256") == {k: v["sha256"] for k, v in plan["inputs"].items()},
         "config/dataset/model identity mismatch")
    need(capture.get("status") in ("completed", "blocked", "failed"), "capture status")
    if capture["status"] != "completed":
        need(isinstance(capture.get("reason"), str) and capture["reason"], "failure reason")
        return {"status": capture["status"], "reason": capture["reason"],
                "capture": capture_record, "metrics": None}
    need(isinstance(capture.get("runtime"), dict), "observed runtime required")
    caps = capture.get("capabilities")
    need(isinstance(caps, list) and all(isinstance(x, str) for x in caps), "capabilities required")
    required = {"stereo_receipt_events", "post_successful_xr_end_frame_events"}
    if not required.issubset(caps):
        return {"status": "blocked", "reason": "required adapter event capabilities absent",
                "capture": capture_record, "metrics": None}
    clock = capture.get("client_clock", {})
    need(isinstance(clock.get("id"), str) and clock["id"] and clock.get("unit") == "ns"
         and clock.get("kind") in ("monotonic", "xr_time", "synthetic"), "client clock")
    if plan["evidence"] == "native_headset":
        need(capture["runtime"].get("device_role") == "headset"
             and clock["kind"] != "synthetic", "headset event domain required")
    need(capture.get("receipt_scope") == "earliest_application_receipt_any_eye",
         "receipt boundary must be explicit")
    window = capture.get("window", {})
    start, end = window.get("start_ns"), window.get("end_ns")
    need(integer(start) and integer(end) and end > start and window.get("clock") == clock["id"],
         "positive same-clock measurement window")
    need(isinstance(capture.get("coverage"), dict)
         and type(capture["coverage"].get("render_tail_complete")) is bool
         and isinstance(capture["coverage"].get("notes"), str), "explicit tail coverage")
    artifacts = capture.get("artifacts", {})
    need(isinstance(artifacts, dict) and artifacts, "artifact index required")
    observed, total = {}, 0
    for name, expected in artifacts.items():
        need(isinstance(expected, dict) and isinstance(expected.get("role"), str)
             and integer(expected.get("bytes")) and bool(HEX.fullmatch(expected.get("sha256", ""))),
             "artifact record")
        actual, _ = regular(confined(root, name), plan["limits"]["max_artifact_bytes"])
        need(actual == {k: expected[k] for k in ("sha256", "bytes")}, "artifact hash/size mismatch")
        total += actual["bytes"]
        need(total <= plan["limits"]["max_total_bytes"], "total artifact budget")
        observed[name] = actual
    def role(name):
        found = [path for path, item in artifacts.items() if item["role"] == name]
        need(len(found) == 1, "one artifact required for role " + name)
        return found[0]
    offered = rows(root, role("offered"), plan["limits"]["max_rows"])
    pairs = rows(root, role("pairs"), plan["limits"]["max_rows"])
    events = rows(root, role("native_events"), plan["limits"]["max_rows"])
    role("runtime")
    role("quality")
    if plan["gaze"]["arm"] != "none":
        role("gaze")
        role("gaze_geometry")
    ledger = {}
    for row in offered:
        identity = ident(row)
        need(identity["pair_id"] not in ledger, "duplicate offered pair")
        ledger[identity["pair_id"]] = identity
    need(integer(capture.get("offered_pairs")) and capture["offered_pairs"] == len(ledger),
         "offered ledger population mismatch")
    pairmap = {}
    for row in pairs:
        identity = ident(row)
        pid = identity["pair_id"]
        need(pid in ledger and identity == ledger[pid] and pid not in pairmap,
             "unknown/duplicate/mismatched pair")
        need(row.get("disposition") in ("candidate", "fallback", "rejected"), "pair disposition")
        need(isinstance(row.get("reason"), str), "pair disposition reason")
        artifact = row.get("candidate_artifact")
        size = row.get("candidate_envelope_bytes")
        if artifact is None:
            need(size is None and row.get("over_cap") is None, "unmeasured bytes must be null")
        else:
            need(artifact in artifacts and artifacts[artifact]["role"] == "candidate_bitstream",
                 "actual candidate envelope artifact required")
            need(integer(size) and size == artifacts[artifact]["bytes"], "candidate byte count")
            cap = plan["stereo_payload_cap_bytes"]
            need(row.get("over_cap") is (size > cap if cap is not None else False), "over-cap decision")
            if row["over_cap"]:
                need(row["disposition"] in ("fallback", "rejected"), "over-cap pair disposition")
        pairmap[pid] = row
    need(set(pairmap) == set(ledger), "offered pairs disappeared")
    receipts, submissions, event_ids = {}, {}, set()
    for event in events:
        need(isinstance(event.get("event_id"), str) and event["event_id"]
             and event["event_id"] not in event_ids, "duplicate/missing event ID")
        event_ids.add(event["event_id"])
        identity = ident(event)
        pid = identity["pair_id"]
        need(pid in ledger and identity == ledger[pid], "event stereo identity")
        need(event.get("clock") == clock["id"] and integer(event.get("timestamp_ns")),
             "receipt/submission clock mismatch")
        stamp = event["timestamp_ns"]
        if event.get("kind") == "receipt":
            receipts[pid] = min(receipts.get(pid, stamp), stamp)
        else:
            need(event.get("kind") == "xr_end_frame_return" and type(event.get("success")) is bool,
                 "event kind/success")
            need(event.get("post_call") is True, "post-call timestamp required")
            need(isinstance(event.get("xr_result"), str) and event["xr_result"]
                 and event["success"] == (event["xr_result"] == "XR_SUCCESS"), "XR result contradiction")
            if event["success"]:
                need(event.get("xr_result") == "XR_SUCCESS", "successful xrEndFrame required")
                target = event.get("presentation_target_id")
                need(isinstance(target, str) and target, "returned presentation target required")
                eyes = event.get("eyes", {})
                need(set(eyes) == {"left", "right"} and all(ident(eyes[e]) == identity
                     and eyes[e].get("presentation_target_id") == target
                     for e in eyes), "mixed or partial stereo submission")
                need(pairmap[pid]["disposition"] != "rejected", "rejected pair submitted")
                submissions.setdefault(pid, []).append(stamp)
    samples, submitted, repeats, source_ids, any_in_window = [], [], 0, set(), set()
    for pid, stamps in submissions.items():
        need(pid in receipts and min(stamps) >= receipts[pid], "missing/late receipt")
        within = sorted(t for t in stamps if start <= t < end)
        if within:
            any_in_window.add(pid)
        repeats += len(within)
        if start <= min(stamps) < end:
            repeats -= 1
            submitted.append(pid)
            source_ids.add(ledger[pid]["source_id"])
            samples.append((min(stamps) - receipts[pid]) / 1e6)
    if plan["gaze"]["arm"] != "none":
        gaze_rows = rows(root, role("gaze"), plan["limits"]["max_rows"])
        gaze = {}
        for row in gaze_rows:
            pid = row.get("pair_id")
            need(pid in ledger and pid not in gaze and row.get("arm") == plan["gaze"]["arm"]
                 and row.get("target_id") == ledger[pid]["target_id"], "gaze identity/arm")
            need(row.get("mode") == plan["gaze"]["mode"], "gaze comparison mode")
            need(row.get("decision") in ("tracked", "fallback")
                 and isinstance(row.get("fallback_policy"), str) and row["fallback_policy"], "gaze decision")
            decision = row.get("decision_ns")
            need(integer(decision) and row.get("decision_clock") == clock["id"], "gaze decision clock")
            need(integer(row.get("presentation_target_ns"))
                 and isinstance(row.get("presentation_target_clock"), str)
                 and row["presentation_target_clock"], "common gaze presentation target time")
            if pid in submissions:
                need(decision <= min(submissions[pid]), "gaze decision after submission")
            for eye in ("left", "right"):
                sample = row.get(eye, {})
                need(type(sample.get("valid")) is bool and type(sample.get("stale")) is bool,
                     "gaze validity/staleness")
                need(isinstance(sample.get("sample_clock"), str) and sample["sample_clock"]
                     and isinstance(sample.get("available_clock"), str) and sample["available_clock"]
                     and integer(sample.get("available_ns")), "gaze availability/sample clock")
                if sample["available_clock"] == clock["id"]:
                    available_upper = sample["available_ns"]
                else:
                    mapping = sample.get("availability_mapping_artifact")
                    need(mapping in artifacts and artifacts[mapping]["role"] == "clock_mapping"
                         and integer(sample.get("available_in_client_ns"))
                         and integer(sample.get("availability_uncertainty_ns")), "mapped gaze availability")
                    available_upper = sample["available_in_client_ns"] + sample["availability_uncertainty_ns"]
                need(available_upper <= decision, "future/unavailable gaze decision input")
                for field in ("acquisition_ns", "pose_expression_ns", "horizon_ns", "uncertainty_ns"):
                    need(field in sample and (sample[field] is None or integer(sample[field])),
                         "explicit gaze timestamp/horizon/uncertainty")
                confidence = sample.get("confidence")
                need("confidence" in sample and (confidence is None or
                     (type(confidence) in (int, float) and math.isfinite(confidence)
                      and 0 <= confidence <= 1)), "gaze confidence")
                calibration = sample.get("calibration_artifact")
                need("calibration_artifact" in sample and (calibration is None or
                     calibration in artifacts), "gaze calibration binding")
                if not sample["valid"] or sample["stale"]:
                    need(row["decision"] == "fallback", "invalid/stale eye requires recorded fallback")
            gaze[pid] = row
        need(set(submissions).issubset(gaze), "submitted gaze decisions missing")
    # All sidecars are rechecked after parsing: later adapters cannot invalidate earlier artifacts.
    for name, expected in observed.items():
        need(regular(confined(root, name), plan["limits"]["max_artifact_bytes"])[0] == expected,
             "artifact changed during validation")
    need(json_file(confined(root, "capture.json"))[1] == capture_record, "capture changed")
    samples.sort()
    p95 = samples[math.ceil(.95 * len(samples)) - 1] if samples else None
    native = plan["evidence"] == "native_headset"
    return {"status": "capture_validated", "capture": capture_record, "artifacts": observed,
            "coverage": capture["coverage"], "metrics": {
                "offered_pairs": len(ledger),
                "rejected_pairs": sum(r["disposition"] == "rejected" for r in pairs),
                "fallback_pairs": sum(r["disposition"] == "fallback" for r in pairs),
                "over_cap_pairs": sum(r.get("over_cap") is True for r in pairs),
                "unmeasured_candidate_bytes_pairs": sum(r.get("candidate_artifact") is None for r in pairs),
                "candidate_serialized_bytes": sum(r["candidate_envelope_bytes"] for r in pairs)
                    if all(r.get("candidate_artifact") is not None for r in pairs) else None,
                "unique_stereo_submissions": len(submitted),
                "unique_source_identities_submitted": len(source_ids),
                "repeat_submissions_in_window": repeats,
                "pairs_with_any_submission_in_window": len(any_in_window),
                "offered_without_submission_in_window": len(ledger) - len(any_in_window),
                "offered_without_first_submission_in_window": len(ledger) - len(submitted),
                "sample_pair_ids": submitted,
                "quantile_method": "nearest_rank; earliest observed successful coherent submission per pair must fall in window",
                "diagnostic_receive_to_submit_p95_ms": p95,
                "headset_receive_to_submit_p95_ms": p95 if native else None,
                "unique_stereo_submissions_per_s": len(submitted) * 1e9 / (end - start),
                "source_age_p95_ms": None,
                "server_encode_p95_ms": None, "server_queue_p95_ms": None,
                "wire_bytes": None}}


def receipt_base(plan, record):
    return {"schema": RECEIPT, "run_id": plan["run_id"], "plan": record,
            "evidence": plan["evidence"], "independent_hardware_verification": False,
            "scope": "supplied adapter artifact integrity and event consistency only; not qualification",
            "limitations": ["No authenticity proof for device assertions or raw logs.",
                "No gate, promotion, perceptual-quality, source-age or gaze-causality verdict.",
                "Unobserved offers/events cannot be recovered; coverage is adapter-declared.",
                "Subprocess duration is never a pipeline latency metric."]}


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def collect(plan_path, root, launch=False):
    path, plan, record, raw, blockers = plan_file(plan_path)
    receipt = receipt_base(plan, record)
    if launch and plan["adapter_argv"] is None:
        blockers.append("manual adapter argv absent")
    if blockers:
        return dict(receipt, status="blocked", reason="; ".join(blockers), metrics=None)
    try:
        before = bind_inputs(path, plan)
        if launch:
            values = {"{plan}": str(path), "{output}": str(root)}
            argv = [values.get(token, token) for token in plan["adapter_argv"]]
            receipt["adapter_argv"] = argv
            with (root / "adapter.stdout").open("xb") as out, (root / "adapter.stderr").open("xb") as err:
                with subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=out,
                                      stderr=err, shell=False, start_new_session=True,
                                      preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE,
                                          (plan["limits"]["max_artifact_bytes"],) * 2)) as process:
                    try:
                        code = process.wait(timeout=plan["limits"]["timeout_seconds"])
                    finally:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
            receipt["adapter_exit_code"] = code
            need(code == 0, "adapter command failed")
        result = validate_capture(plan, record["sha256"], root)
        need(bind_inputs(path, plan) == before and json_file(path)[1] == record,
             "plan/input changed during capture validation")
        return dict(receipt, inputs=before, **result)
    except (Exception, KeyboardInterrupt) as error:
        return dict(receipt, status="failed", error=type(error).__name__ + ": " + str(error), metrics=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("plan")
    for command in ("run", "import"):
        p = sub.add_parser(command)
        p.add_argument("plan")
        if command == "import":
            p.add_argument("capture_directory")
        p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            _, plan, record, _, blockers = plan_file(args.plan)
            print(json.dumps(dict(receipt_base(plan, record), status="planned", blockers=blockers,
                                  manual_adapter_present=plan["adapter_argv"] is not None), indent=2))
            return 0
        destination = Path(args.output).absolute()
        need(not destination.exists() and not destination.is_symlink(), "output already exists")
        if args.command == "run":
            root = destination.parent.resolve() / destination.name
            root.mkdir(mode=0o700)
            receipt = collect(args.plan, root, True)
            save(root / "capture-validation.json", receipt)
        else:
            root = Path(args.capture_directory).absolute()
            receipt = collect(args.plan, root)
            save(destination, receipt)
        print(json.dumps(receipt, indent=2))
        return 0 if receipt["status"] == "capture_validated" else 2
    except (Exception, KeyboardInterrupt) as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
