"""Validate evidence records; validation never qualifies or promotes a codec."""
import argparse
import json
import math
import re
from pathlib import Path


class InvalidManifest(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidManifest(message)


def fields(value, names, where):
    require(isinstance(value, dict), f"{where}: expected object")
    require(set(value) == set(names.split()), f"{where}: unexpected or missing fields")


def label(value, where):
    require(isinstance(value, str) and bool(value.strip()), f"{where}: expected text")


def digest(value, length, where, optional=False):
    require(optional and value is None or isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value),
            f"{where}: expected immutable lowercase {length}-hex digest")


def number(value, where, optional=True, integer=False):
    if optional and value is None:
        return
    require((type(value) is int or type(value) is float and math.isfinite(value)) and value >= 0,
            f"{where}: expected finite nonnegative number")
    require(not integer or type(value) is int, f"{where}: expected integer")


def validate(record):
    fields(record, "schema_version status evidence execution provenance candidate dataset "
           "bitstream profile transport hardware identity observations limitations", "manifest")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1,
            "unsupported schema_version")
    require(record["status"] in ("planned", "completed", "failed"), "invalid status")
    evidence_execution = {"offline_correctness": "offline", "simulation": "simulation",
                          "host_timing": "hardware", "headset_timing": "hardware"}
    require(record["evidence"] in evidence_execution, "invalid evidence")
    require(record["execution"] == evidence_execution[record["evidence"]],
            "simulation/offline and hardware evidence must remain distinct")
    require(isinstance(record["limitations"], list), "limitations must be a list")
    for limitation in record["limitations"]:
        label(limitation, "limitation")
    complete = record["status"] == "completed"

    provenance = record["provenance"]
    fields(provenance, "candidate_commit baseline_commit harness_commit configuration_sha256 artifacts_sha256",
           "provenance")
    for name in ("candidate_commit", "baseline_commit", "harness_commit"):
        digest(provenance[name], 40, name)
    digest(provenance["configuration_sha256"], 64, "configuration_sha256", not complete)
    artifacts = provenance["artifacts_sha256"]
    require(isinstance(artifacts, dict), "artifacts_sha256 must map artifact names to hashes")
    require(not complete or "encoded_streams" in artifacts, "completed run needs encoded artifact hash")
    for name, value in artifacts.items():
        label(name, "artifact name")
        digest(value, 64, f"artifact {name}")
    candidate = record["candidate"]
    fields(candidate, "architecture learned model_sha256 uncertainty", "candidate")
    label(candidate["architecture"], "architecture")
    require(type(candidate["learned"]) is bool, "learned must be explicit")
    if candidate["learned"]:
        digest(candidate["model_sha256"], 64, "model_sha256")
        require(candidate["uncertainty"] in ("exploratory", "trained_unqualified"),
                "learned models require an uncertainty label")
    else:
        require(candidate["model_sha256"] is None
                and candidate["uncertainty"] == "not_applicable",
                "nonlearned candidates use null model hash and not_applicable uncertainty")

    dataset = record["dataset"]
    fields(dataset, "sha256 splits", "dataset")
    digest(dataset["sha256"], 64, "dataset.sha256", not complete)
    fields(dataset["splits"], "train validation test", "splits")
    scenes, sessions = {}, {}
    for split, entries in dataset["splits"].items():
        require(isinstance(entries, list), f"{split}: expected list")
        seen = set()
        for entry in entries:
            fields(entry, "scene session", split)
            for key, owners in (("scene", scenes), ("session", sessions)):
                label(entry[key], key)
                require(entry[key] not in owners or owners[entry[key]] == split,
                        f"{key} leakage across splits: {entry[key]}")
                owners[entry[key]] = split
            pair = (entry["scene"], entry["session"])
            require(pair not in seen, "duplicate scene/session entry")
            seen.add(pair)
    require(not complete or dataset["splits"]["test"], "completed run needs held-out test scenes")

    bitstream = record["bitstream"]
    fields(bitstream, "identifier version quality_contract", "bitstream")
    label(bitstream["identifier"], "bitstream.identifier")
    label(bitstream["version"], "bitstream.version")
    require(bitstream["quality_contract"] in ("q8_exact", "new_lossy", "lossless"),
            "invalid quality contract")
    profile = record["profile"]
    fields(profile, "width_per_eye height_per_eye offered_hz gate_reference", "profile")
    for name in ("width_per_eye", "height_per_eye", "offered_hz"):
        number(profile[name], name, optional=False, integer=name != "offered_hz")
        require(profile[name] > 0, f"{name}: must be positive")
    require(profile["gate_reference"] in (None, "cpu-rgb444-sub15-v1"), "unknown gate reference")
    if profile["gate_reference"]:
        require((profile["width_per_eye"], profile["height_per_eye"], profile["offered_hz"])
                == (576, 640, 90), "gate reference profile mismatch")
    transport = record["transport"]
    fields(transport, "mode application_cap_bytes framing", "transport")
    require(transport["mode"] in ("file", "native_tcp", "native_tcp_adb",
                                 "datagram_simulation", "datagram_hardware"), "unknown transport")
    label(transport["framing"], "framing")
    number(transport["application_cap_bytes"], "application_cap_bytes", integer=True)
    require(transport["application_cap_bytes"] != 0, "application cap must be positive or null")
    if complete and transport["mode"].startswith("datagram_"):
        require(transport["application_cap_bytes"] is not None, "datagram evidence needs an explicit cap")
    require(transport["mode"] != "datagram_simulation" or record["execution"] == "simulation",
            "datagram model must be labeled simulation")
    hardware = record["hardware"]
    fields(hardware, "server headset render_load", "hardware")
    for name, value in hardware.items():
        if value is not None:
            label(value, f"hardware.{name}")
    if complete and record["execution"] == "hardware":
        require(hardware["server"] is not None, "hardware timing needs server identity")
        if record["evidence"] == "headset_timing":
            require(hardware["headset"] is not None and hardware["render_load"] is not None,
                    "headset timing needs headset identity and rendering load")
    identity = record["identity"]
    fields(identity, "unit fields reject_partial", "identity")
    require(identity == {"unit": "stereo_pair",
                         "fields": ["stereo_pair_id", "source_frame_id", "warp_id"],
                         "reject_partial": True} and identity["reject_partial"] is True,
            "whole stereo/source/warp identity is required")

    observations = record["observations"]
    if observations is None:
        require(not complete, "completed run needs observations")
        return
    require(record["status"] != "planned", "planned runs cannot contain measured observations")
    counters = ("offered_pairs rejected_pairs fallback_pairs over_cap_pairs serialized_bytes "
                "wire_bytes max_stereo_serialized_bytes").split()
    timings = ("server_encode_p95_ms server_queue_p95_ms headset_decode_p95_ms "
               "headset_receive_to_submit_p95_ms unique_stereo_submissions_per_s "
               "source_age_p95_ms").split()
    fields(observations, " ".join(counters + timings) +
           " source_age_clock_basis stereo_identity_verified population quality", "observations")
    for name in counters + timings:
        number(observations[name], name, integer=name in counters)
    for name in counters[:5]:
        require(not complete or observations[name] is not None, f"completed run needs {name}")
    verified = observations["stereo_identity_verified"]
    require(verified is None or type(verified) is bool, "identity observation must be boolean/null")
    require(not complete or verified is not None, "completed run must check stereo identity")
    offered, rejected, fallback, over_cap = (observations[name] for name in counters[:4])
    if all(value is not None for value in (offered, rejected, fallback, over_cap)):
        require(rejected + fallback <= offered and over_cap <= rejected + fallback,
                "rejected/fallback must account for over-cap whole pairs without double counting")
    cap, peak = transport["application_cap_bytes"], observations["max_stereo_serialized_bytes"]
    total = observations["serialized_bytes"]
    require(peak is None or total is None or peak <= total, "peak bytes cannot exceed total bytes")
    if cap is not None and peak is not None and peak > cap:
        require(over_cap is not None and over_cap > 0, "over-cap payload needs counted disposition")
    basis = observations["source_age_clock_basis"]
    require(basis in (None, "same_clock_server", "same_clock_headset",
                      "synchronized_server_headset", "simulation_clock"), "unknown source-age clock")
    require(basis != "simulation_clock" or record["execution"] == "simulation",
            "simulation clock cannot label hardware source age")
    require(observations["source_age_p95_ms"] is None or basis is not None,
            "source age requires a known clock basis")
    if record["evidence"] in ("offline_correctness", "simulation", "host_timing"):
        require(observations["headset_receive_to_submit_p95_ms"] is None
                and observations["headset_decode_p95_ms"] is None,
                "headset timing fields require headset hardware evidence")
    if complete and record["evidence"] == "host_timing":
        require(observations["server_encode_p95_ms"] is not None, "host timing needs encode timing")
    if complete and record["evidence"] == "headset_timing":
        require(observations["headset_receive_to_submit_p95_ms"] is not None,
                "headset timing needs receive-to-submit timing")
    population = observations["population"]
    fields(population, "definition window_s server_pairs headset_pairs", "population")
    label(population["definition"], "population.definition")
    for name in ("window_s", "server_pairs", "headset_pairs"):
        number(population[name], f"population.{name}", integer=name != "window_s")
    if complete and record["execution"] == "hardware":
        require(population["window_s"] is not None and population["window_s"] > 0,
                "hardware timing needs a measured time window")
        scope = "headset_pairs" if record["evidence"] == "headset_timing" else "server_pairs"
        require(population[scope] is not None and population[scope] > 0,
                "hardware timing needs a measured sample population")
    quality = observations["quality"]
    fields(quality, "metric value reference", "quality")
    for name in ("metric", "reference"):
        if quality[name] is not None:
            label(quality[name], f"quality.{name}")
    number(quality["value"], "quality.value",
           integer=quality["metric"] == "exact_mismatched_samples")
    require(quality["value"] is None or all(quality[name] is not None
                                          for name in ("metric", "reference")),
            "quality value needs metric and reference")
    if complete and record["evidence"] == "offline_correctness":
        require(quality["value"] is not None, "correctness evidence needs measured quality")
        if bitstream["quality_contract"] in ("q8_exact", "lossless"):
            require(quality["metric"] == "exact_mismatched_samples",
                    "exact reconstruction needs exact mismatch count")


def load(path):
    def invalid_constant(value):
        raise InvalidManifest(f"non-finite JSON constant: {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    with Path(path).open(encoding="utf-8") as stream:
        record = json.load(stream, parse_constant=invalid_constant, object_pairs_hook=unique_object)
    validate(record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        record = load(args.manifest)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"invalid manifest: {error}\n")
    print(f"Valid {record['status']} evidence structure; no qualification or promotion asserted.")


if __name__ == "__main__":
    main()
