#!/usr/bin/env python3
"""Explicit trusted native adapter hook. POSIX only; not a sandbox or live harness."""
import argparse
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import core

RUNNER = Path(__file__).resolve().parents[1] / "research_workflow" / "run_pair.py"
spec = importlib.util.spec_from_file_location("decision_pair_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def pinned_file(record, limit=256 * 1024 * 1024):
    core.require(set(record) == {"path", "sha256"}, "expected path/sha256")
    path = Path(record["path"])
    core.require(path.is_absolute(), "native artifact paths must be absolute")
    core.digest_string(record["sha256"])
    data = runner.read_regular(path, limit)
    core.require(core.sha256(data) == record["sha256"], "native artifact hash mismatch")
    return data


def run(config_path, output):
    config_path = Path(config_path)
    config_bytes = config_path.read_bytes()
    config = core.parse_json(config_bytes)
    core.require(set(config) == {"schema_version", "profile", "quality_contract", "provenance",
                                "identity", "width", "height", "inputs", "policy", "model",
                                "encoder_argv", "decoder_argv", "artifacts", "payload_cap_bytes",
                                "timeout_seconds"}, "unexpected native configuration fields")
    core.require(config["schema_version"] == 1 and config["profile"] == core.PROFILE,
                 "unsupported native profile")
    core.require(config["quality_contract"] in ("q8_exact", "lossless"), "exact reference required")
    pins = config["provenance"]
    core.require(set(pins) == {"candidate_commit", "baseline_commit", "harness_commit",
                              "dataset_index_sha256"}, "missing native provenance")
    for key, value in pins.items():
        core.digest_string(value, 64 if key == "dataset_index_sha256" else 40)
    identity = config["identity"]
    core.require(set(identity) == {"stereo_pair_id", "source_frame_id", "warp_id"} and
                 all(isinstance(v, str) and v for v in identity.values()), "incomplete identity")
    core.integer(config["width"], 1, 4096)
    core.integer(config["height"], 1, 4096)
    core.integer(config["payload_cap_bytes"], 1, 32 * 1024 * 1024)
    timeout = runner.positive_seconds(config["timeout_seconds"])
    core.require(timeout <= 300, "native timeout exceeds 300 seconds")
    core.require(set(config["inputs"]) == {"left", "right"}, "whole stereo input required")
    inputs = {eye: pinned_file(entry, 16 * 1024 * 1024) for eye, entry in config["inputs"].items()}
    artifacts = config["artifacts"]
    core.require(isinstance(artifacts, list) and bool(artifacts), "pin native executable artifacts")
    for entry in artifacts:
        pinned_file(entry)
    known_paths = {str(Path(entry["path"])) for entry in artifacts}
    for stage in ("encoder_argv", "decoder_argv"):
        argv = config[stage]
        core.require(isinstance(argv, list) and argv and
                     all(isinstance(v, str) and "\0" not in v for v in argv), "argv must be strings")
        core.require(argv[0] in known_paths, "command executable missing from artifact pins")
    core.require("{plan}" in config["encoder_argv"], "encoder requires standalone {plan}")
    core.require("{plan}" not in config["decoder_argv"], "decoder must not receive encoder plan")
    model = None
    if config["policy"] == "mlp":
        core.require(config["model"] is not None, "learned policy requires pinned model")
        model_bytes = pinned_file(config["model"], 1024 * 1024)
        model = core.validate_model(core.parse_json(model_bytes))
        core.require(model["quality_contract"] == config["quality_contract"] and
                     model["dataset_sha256"] == pins["dataset_index_sha256"], "model contract mismatch")
    else:
        core.require(config["model"] is None, "control must explicitly use null model")
    plans = {eye: core.plan(rgb, config["width"], config["height"], config["policy"], model)
             for eye, rgb in inputs.items()}
    directory = Path(output).absolute()
    directory.mkdir(mode=0o700)
    core.write_json(directory / "config.json", config)
    for eye in inputs:
        (directory / (eye + ".rgb")).write_bytes(inputs[eye])
        plans[eye]["identity"] = identity
        plans[eye]["eye"] = eye
        core.write_json(directory / (eye + ".plan.json"), plans[eye])
    # Existing runner supplies bounded separate processes, exact-size decodes and final artifact checks.
    encoder = [str(directory / "{eye}.plan.json") if token == "{plan}" else token
               for token in config["encoder_argv"]]
    args = SimpleNamespace(
        output=str(directory / "pair"), left=str(directory / "left.rgb"), right=str(directory / "right.rgb"),
        width=config["width"], height=config["height"], encoder_argv=encoder,
        decoder_argv=config["decoder_argv"], timeout_seconds=timeout,
        max_encoded_bytes=16 * 1024 * 1024, max_decoded_bytes=16 * 1024 * 1024,
        stereo_payload_cap=config["payload_cap_bytes"])
    exit_code = runner.run_pair(args)
    pair_path = directory / "pair" / "receipt.json"
    pair = core.read_json(pair_path)
    source_match = all(pair["eyes"][eye].get("snapshot", {}).get("sha256") ==
                       core.sha256(inputs[eye]) for eye in inputs)
    exact = all(pair["eyes"][eye].get("rgb8_error", {}).get("exact_equal") is True for eye in inputs)
    pins_intact = True
    try:
        for entry in artifacts + ([config["model"]] if config["model"] else []):
            pinned_file(entry)
        for eye in inputs:
            core.require(core.read_json(directory / (eye + ".plan.json")) == plans[eye], "plan changed")
    except (ValueError, OSError):
        pins_intact = False
    valid = pair["status"] in ("completed", "rejected") and source_match and exact and pins_intact
    admitted = valid and pair["stereo"]["admitted"]
    total = pair["stereo"].get("encoded_payload_bytes")
    receipt = {
        "schema": "wivrn-native-decision-hook-v1", "evidence": "offline_external_adapter",
        "status": pair["status"] if valid else "failed", "config_sha256": core.sha256(config_bytes),
        "pair_receipt_sha256": core.sha256(pair_path.read_bytes()), "identity": identity,
        "quality_contract": config["quality_contract"], "provenance": pins,
        "model_file_sha256": config["model"]["sha256"] if config["model"] else None,
        "requested_plan_sha256": {eye: core.sha256(core.canonical(plans[eye])) for eye in inputs},
        "exact_reference_pixels": exact, "source_plan_match": source_match,
        "artifacts_unchanged": pins_intact, "decision_application_verified": False,
        "offered_pairs": 1, "rejected_pairs": 0 if admitted else 1, "fallback_pairs": 0,
        "over_cap_pairs": int(total > config["payload_cap_bytes"]) if total is not None else None,
        "encoded_eye_payload_bytes": total, "admitted_eyes": ["left", "right"] if admitted else [],
        "wire_bytes": None, "unique_stereo_submissions_per_s": None,
        "headset_receive_to_submit_p95_ms": None,
        "limitations": ["External adapters are trusted commands; this is not a sandbox.",
                        "Exact comparison is against supplied RGB8 reference, not original pre-Q8 pixels.",
                        "Adapter plan compliance, native RRB grammar and wire identity need native receipts.",
                        "Eye output byte sum excludes any additional stereo envelope or transport.",
                        "Subprocess durations include startup/I/O; no live or headset timing."]}
    core.write_json(directory / "decision-receipt.json", receipt)
    return exit_code if valid else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, help="new directory; never overwritten")
    args = parser.parse_args()
    return run(args.config, args.output)


if __name__ == "__main__":
    sys.exit(main())
