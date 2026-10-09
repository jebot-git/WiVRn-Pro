#!/usr/bin/env python3
"""Replaceable, standard-library RRB32 mode predictor; no native codec implementation."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random

MODES = ("raw", "plain", "up")
FEATURES = ("mean", "variance", "horizontal_delta", "vertical_delta",
            "zero_fraction", "horizontal_equal", "vertical_equal", "rows")
PROFILE = "rrb32-rgb8-mode-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def pairs_unique(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=pairs_unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def read_json(path):
    return parse_json(Path(path).read_bytes())


def write_json(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value))


def digest_string(value, length=64):
    require(isinstance(value, str) and len(value) == length and
            all(c in "0123456789abcdef" for c in value), "invalid immutable digest")


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "integer out of bounds")


def artifact(root, record, limit=16 * 1024 * 1024):
    require(set(record) == {"path", "sha256"}, "artifact requires path and sha256")
    digest_string(record["sha256"])
    name = Path(record["path"])
    require(not name.is_absolute() and ".." not in name.parts, "artifact must be relative")
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), "artifact escaped index root")
    require(path.stat().st_size <= limit, "artifact exceeds budget")
    data = path.read_bytes()
    require(sha256(data) == record["sha256"], "artifact digest mismatch")
    return data


def features(rgb, width, rows):
    integer(width, 1, 4096)
    integer(rows, 1, 32)
    require(len(rgb) == width * rows * 3, "invalid RGB8 block size")
    n = len(rgb)
    mean = sum(rgb) / n
    horizontal = [(rgb[i], rgb[i - 3]) for i in range(n) if i % (width * 3) >= 3]
    vertical = [(rgb[i], rgb[i - width * 3]) for i in range(width * 3, n)]
    def delta(items):
        return sum(abs(a - b) for a, b in items) / (255 * len(items)) if items else 0.0
    def equal(items):
        return sum(a == b for a, b in items) / len(items) if items else 0.0
    return [mean / 255, sum((v - mean) ** 2 for v in rgb) / (n * 255 ** 2),
            delta(horizontal), delta(vertical), rgb.count(0) / n,
            equal(horizontal), equal(vertical), rows / 32]


def load_dataset(path, expected_sha256):
    path = Path(path)
    digest_string(expected_sha256)
    raw = path.read_bytes()
    require(sha256(raw) == expected_sha256, "frozen index digest mismatch")
    data = parse_json(raw)
    require(set(data) == {"schema_version", "profile", "quality_contract", "evidence",
                          "provenance", "records"}, "unexpected dataset fields")
    require(data["schema_version"] == 1 and data["profile"] == PROFILE, "unknown dataset profile")
    require(data["quality_contract"] in ("q8_exact", "lossless"), "selector requires exact reference")
    require(data["evidence"] in ("synthetic_smoke", "offline_native"), "unsupported evidence domain")
    pins = data["provenance"]
    require(set(pins) == {"candidate_commit", "baseline_commit", "harness_commit", "artifacts"},
            "missing provenance")
    for name in ("candidate_commit", "baseline_commit", "harness_commit"):
        digest_string(pins[name], 40)
    require(isinstance(pins["artifacts"], list), "artifacts must be a list")
    if data["evidence"] == "offline_native":
        require(len(pins["artifacts"]) >= 2, "pin encoder and decoder artifacts")
    for entry in pins["artifacts"]:
        artifact(path.parent, entry)
    require(isinstance(data["records"], list) and 0 < len(data["records"]) <= 100000,
            "invalid record population")
    scenes, sessions, pairs, ids, sources = {}, {}, {}, set(), {}
    records = []
    for item in data["records"]:
        require(set(item) == {"id", "scene", "session", "split", "stereo_pair_id",
                              "source_frame_id", "warp_id", "eye", "block_index", "width",
                              "rows", "source", "outcomes"}, "unexpected record fields")
        for name in ("id", "scene", "session", "stereo_pair_id", "source_frame_id", "warp_id"):
            require(isinstance(item[name], str) and bool(item[name]), "empty identity")
        require(item["id"] not in ids, "duplicate record id")
        ids.add(item["id"])
        split = item["split"]
        require(split in ("train", "validation", "test"), "invalid split")
        for mapping, key in ((scenes, item["scene"]), (sessions, item["session"])):
            require(mapping.setdefault(key, split) == split, "scene/session leakage")
        pair_key = (item["session"], item["stereo_pair_id"])
        identity = (item["scene"], split, item["source_frame_id"], item["warp_id"], item["width"])
        pair = pairs.setdefault(pair_key, {"identity": identity, "eyes": {"left": {}, "right": {}}})
        require(pair["identity"] == identity, "mixed stereo identity")
        require(item["eye"] in ("left", "right"), "invalid eye")
        integer(item["block_index"], 0, 127)
        integer(item["rows"], 1, 32)
        blocks = pair["eyes"][item["eye"]]
        require(item["block_index"] not in blocks, "duplicate eye block")
        blocks[item["block_index"]] = item["rows"]
        rgb = artifact(path.parent, item["source"])
        require(sources.setdefault(item["source"]["sha256"], split) == split,
                "identical source content across splits")
        x = features(rgb, item["width"], item["rows"])
        require(set(item["outcomes"]) == set(MODES), "need all matched mode outcomes")
        sizes = []
        for mode in MODES:
            outcome = item["outcomes"][mode]
            require(set(outcome) == {"encoded", "decoded"}, "unexpected outcome fields")
            bits = artifact(path.parent, outcome["encoded"])
            require(bool(bits), "empty encoded artifact")
            require(artifact(path.parent, outcome["decoded"]) == rgb, "mode failed exact oracle")
            sizes.append(len(bits))
        records.append(dict(item, x=x, sizes=sizes))
    for pair in pairs.values():
        left, right = pair["eyes"]["left"], pair["eyes"]["right"]
        require(left and left == right, "incomplete stereo block population")
        require(sorted(left) == list(range(len(left))), "missing block index")
        require(all(left[i] == 32 for i in range(len(left) - 1)), "short nonfinal block")
    return data, records


def validate_model(model):
    require(set(model) == {"schema_version", "architecture", "profile", "features", "modes",
                          "dataset_sha256", "train_ids_sha256", "evidence", "quality_contract",
                          "provenance", "seed", "epochs", "learning_rate", "w1", "b1", "w2", "b2"},
            "unexpected model fields")
    require(model["schema_version"] == 1 and model["architecture"] == "tanh-mlp-v1" and
            model["profile"] == PROFILE and model["features"] == list(FEATURES) and
            model["modes"] == list(MODES), "incompatible model architecture")
    digest_string(model["dataset_sha256"])
    digest_string(model["train_ids_sha256"])
    require(model["evidence"] in ("synthetic_smoke", "offline_native"), "model evidence")
    require(model["quality_contract"] in ("q8_exact", "lossless"), "model quality")
    integer(model["epochs"], 1, 2000)
    integer(model["seed"], 0, 2 ** 32 - 1)
    require(type(model["learning_rate"]) in (int, float) and
            math.isfinite(model["learning_rate"]) and 0 < model["learning_rate"] <= 1, "learning rate")
    hidden = len(model["b1"])
    integer(hidden, 1, 64)
    require(len(model["w1"]) == hidden and len(model["w2"]) == 3 and len(model["b2"]) == 3,
            "invalid model shape")
    require(all(len(row) == 8 for row in model["w1"]) and
            all(len(row) == hidden for row in model["w2"]), "invalid model matrix")
    for row in model["w1"] + model["w2"] + [model["b1"], model["b2"]]:
        require(all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e6 for v in row),
                "nonfinite or excessive model weight")
    return model


def forward(model, x):
    h = [math.tanh(sum(w * v for w, v in zip(row, x)) + b)
         for row, b in zip(model["w1"], model["b1"])]
    logits = [sum(w * v for w, v in zip(row, h)) + b
              for row, b in zip(model["w2"], model["b2"])]
    probs = [math.exp(v - max(logits)) for v in logits]
    total = sum(probs)
    return h, [v / total for v in probs]


def train(data, records, index_sha, seed=7, epochs=40, hidden=8, learning_rate=0.05):
    integer(seed, 0, 2 ** 32 - 1)
    integer(epochs, 1, 2000)
    integer(hidden, 1, 64)
    require(math.isfinite(learning_rate) and 0 < learning_rate <= 1, "learning rate")
    rows = [r for r in records if r["split"] == "train"]
    require(bool(rows), "no training records")
    rng = random.Random(seed)
    model = dict(schema_version=1, architecture="tanh-mlp-v1", profile=PROFILE,
                 features=list(FEATURES), modes=list(MODES), dataset_sha256=index_sha,
                 train_ids_sha256=sha256(canonical(sorted(r["id"] for r in rows))),
                 evidence=data["evidence"], quality_contract=data["quality_contract"],
                 provenance=data["provenance"], seed=seed, epochs=epochs, learning_rate=learning_rate,
                 w1=[[rng.uniform(-0.2, 0.2) for _ in FEATURES] for _ in range(hidden)],
                 b1=[0.0] * hidden, w2=[[rng.uniform(-0.2, 0.2) for _ in range(hidden)] for _ in MODES],
                 b2=[0.0] * 3)
    # Only train rows enter gradients. Held-out labels do not select epochs or architecture.
    for _ in range(epochs):
        order = list(rows)
        rng.shuffle(order)
        for row in order:
            x = row["x"]
            h, p = forward(model, x)
            label = min(range(3), key=lambda i: row["sizes"][i])
            dz = [p[i] - (i == label) for i in range(3)]
            dh = [(1 - h[j] ** 2) * sum(dz[i] * model["w2"][i][j] for i in range(3))
                  for j in range(hidden)]
            for i in range(3):
                model["b2"][i] -= learning_rate * dz[i]
                for j in range(hidden):
                    model["w2"][i][j] -= learning_rate * dz[i] * h[j]
            for j in range(hidden):
                model["b1"][j] -= learning_rate * dh[j]
                for k in range(8):
                    model["w1"][j][k] -= learning_rate * dh[j] * x[k]
    return validate_model(model)


def evaluate(model, data, records, index_sha, split="test"):
    validate_model(model)
    require(split in ("validation", "test"), "evaluation requires held-out split")
    require(model["dataset_sha256"] == index_sha and model["evidence"] == data["evidence"] and
            model["quality_contract"] == data["quality_contract"], "model/index mismatch")
    train_ids = sorted(r["id"] for r in records if r["split"] == "train")
    require(model["train_ids_sha256"] == sha256(canonical(train_ids)), "training membership changed")
    rows = [r for r in records if r["split"] == split]
    require(bool(rows), "empty evaluation split")
    totals = {name: 0 for name in ("mlp", "fixed_raw", "fixed_plain", "fixed_up", "adaptive_bytes")}
    choices, hits = [], 0
    for row in rows:
        _, p = forward(model, row["x"])
        chosen = max(range(3), key=lambda i: p[i])
        oracle = min(range(3), key=lambda i: row["sizes"][i])
        hits += row["sizes"][chosen] == row["sizes"][oracle]
        totals["mlp"] += row["sizes"][chosen]
        totals["adaptive_bytes"] += row["sizes"][oracle]
        for i, mode in enumerate(MODES):
            totals["fixed_" + mode] += row["sizes"][i]
        choices.append({"id": row["id"], "mode": MODES[chosen], "probabilities": p,
                        "selected_block_bytes": row["sizes"][chosen]})
    return {"schema": "wivrn-mode-evaluation-v1", "evidence": data["evidence"], "split": split,
            "dataset_sha256": index_sha, "model_canonical_sha256": sha256(canonical(model)),
            "blocks": len(rows), "byte_optimal_choices": hits, "selected_block_bytes": totals,
            "choices": choices, "serialized_stereo_bytes": None, "wire_bytes": None,
            "over_cap_pairs": None, "unique_stereo_submissions_per_s": None,
            "headset_receive_to_submit_p95_ms": None,
            "limitations": ["Counterfactual block-artifact byte totals, not assembled RRB or transport.",
                            "Adaptive control tries every mode; its CPU cost is not measured here.",
                            "No timing, cap, stereo submission, calibration, or device qualification."]}


def plan(rgb, width, height, policy, model=None):
    integer(width, 1, 4096)
    integer(height, 1, 4096)
    require(len(rgb) == width * height * 3, "invalid RGB8 eye size")
    require(policy in ("mlp", "fixed_raw", "fixed_plain", "fixed_up", "adaptive_bytes"),
            "unknown policy")
    if policy == "mlp":
        require(model is not None, "mlp policy requires a model")
        validate_model(model)
    else:
        require(model is None, "controls must not receive a model")
    blocks = []
    for index, start in enumerate(range(0, height, 32)):
        rows = min(32, height - start)
        block = rgb[start * width * 3:(start + rows) * width * 3]
        if policy == "mlp":
            _, p = forward(model, features(block, width, rows))
            mode = MODES[max(range(3), key=lambda i: p[i])]
        else:
            mode = "adaptive_bytes" if policy == "adaptive_bytes" else policy[6:]
            p = None
        blocks.append({"index": index, "rows": rows, "mode": mode, "probabilities": p})
    return {"schema": "wivrn-rrb32-decision-plan-v1", "profile": PROFILE,
            "source_sha256": sha256(rgb), "width": width, "height": height, "policy": policy,
            "model_canonical_sha256": sha256(canonical(model)) if model is not None else None,
            "blocks": blocks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("train", "evaluate"):
        p = sub.add_parser(command)
        p.add_argument("--index", required=True)
        p.add_argument("--index-sha256", required=True)
        p.add_argument("--output", required=True)
        if command == "train":
            p.add_argument("--epochs", type=int, default=40)
            p.add_argument("--hidden", type=int, default=8)
            p.add_argument("--seed", type=int, default=7)
        else:
            p.add_argument("--model", required=True)
            p.add_argument("--split", choices=("validation", "test"), default="test")
    p = sub.add_parser("predict")
    p.add_argument("--input", required=True)
    p.add_argument("--model")
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--policy", choices=("mlp", "fixed_raw", "fixed_plain", "fixed_up", "adaptive_bytes"),
                   default="mlp")
    p.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "predict":
        model_bytes = Path(args.model).read_bytes() if args.model else None
        model = parse_json(model_bytes) if model_bytes is not None else None
        output = plan(Path(args.input).read_bytes(), args.width, args.height, args.policy, model)
        output["model_file_sha256"] = sha256(model_bytes) if model_bytes is not None else None
    else:
        data, records = load_dataset(args.index, args.index_sha256)
        if args.command == "train":
            output = train(data, records, args.index_sha256, args.seed, args.epochs, args.hidden)
        else:
            model_bytes = Path(args.model).read_bytes()
            output = evaluate(parse_json(model_bytes), data, records, args.index_sha256, args.split)
            output["model_file_sha256"] = sha256(model_bytes)
    write_json(args.output, output)


if __name__ == "__main__":
    main()
