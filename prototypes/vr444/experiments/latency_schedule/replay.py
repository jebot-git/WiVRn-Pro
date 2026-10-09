#!/usr/bin/env python3
"""Offline, observational timing analysis; frontier is NOT a performance replay.

Only already-observed submitted pairs have absolute publication timestamps.
Never treats first-packet arrival as complete payload or predicted display as photons.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name}: expected integer >= {minimum}")
    return value


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    def percentile(p):
        return values[max(0, math.ceil(p * len(values)) - 1)]
    return dict(n=len(values), min=values[0], p50=percentile(.5),
                p95=percentile(.95), p99=percentile(.99), max=values[-1])


@dataclass(frozen=True)
class Pair:
    frame: int
    warp: int
    receive: tuple
    ready: tuple

    @property
    def identity(self):
        return self.frame, self.warp

    @property
    def complete(self):
        return max(self.ready)


def parse_present(rows):
    pairs, events = {}, []
    previous = -1
    for row in rows:
        for key in ("left", "right", "left_warp", "right_warp", "submit_xr_ns",
                    "predicted_display_xr_ns", "left_receive_xr_ns", "right_receive_xr_ns",
                    "left_decode_ready_xr_ns", "right_decode_ready_xr_ns"):
            integer(row[key], key, 1 if key.endswith("xr_ns") else 0)
        if row["left"] != row["right"] or row["left_warp"] != row["right_warp"]:
            raise ValueError("stereo identity mismatch")
        pair = Pair(row["left"], row["left_warp"],
                    (row["left_receive_xr_ns"], row["right_receive_xr_ns"]),
                    (row["left_decode_ready_xr_ns"], row["right_decode_ready_xr_ns"]))
        submit = row["submit_xr_ns"]
        if submit <= previous:
            raise ValueError("submission times must strictly increase")
        if any(a > b for a, b in zip(pair.receive, pair.ready)) or pair.complete > submit:
            raise ValueError("receive/publication/submit order violation")
        if pair.frame in pairs and pairs[pair.frame] != pair:
            raise ValueError("frame identity or timestamp changed between repeats")
        pairs[pair.frame] = pair
        events.append((submit, row["predicted_display_xr_ns"], pair))
        previous = submit
    return list(pairs.values()), events


def frontier(pairs, events, margin_ns, ttl_ns=None):
    """Hindsight known-pair candidate set; ignores real buffer residency.

    Synthetic selection cutoff is recorded xrEndFrame return minus margin.
    Deadline/TTL controls eligibility, never manufactures an incomplete eye.
    A rejected refresh repeats the previous pair, which can itself be stale.
    """
    integer(margin_ns, "margin_ns")
    if ttl_ns is not None:
        integer(ttl_ns, "ttl_ns")
    prior = None
    changed = ahead = behind = empty = repeats = stale_repeats = 0
    shifts, ages = [], []
    for submit, _, actual in events:
        cutoff = submit - margin_ns
        candidates = [p for p in pairs if p.complete <= cutoff and
                      (prior is None or p.frame >= prior.frame) and
                      (ttl_ns is None or cutoff - min(p.receive) <= ttl_ns)]
        selected = max(candidates, key=lambda p: p.frame) if candidates else prior
        if selected is None:
            empty += 1
            continue
        if prior == selected:
            repeats += 1
            if ttl_ns is not None and cutoff - min(selected.receive) > ttl_ns:
                stale_repeats += 1
        changed += selected.identity != actual.identity
        ahead += selected.frame > actual.frame
        behind += selected.frame < actual.frame
        # Signed first-packet age difference; NOT end-to-end latency saved.
        shifts.append((min(selected.receive) - min(actual.receive)) / 1e6)
        ages.append((submit - min(selected.receive)) / 1e6)
        prior = selected
    return dict(model="incomplete_observed_pairs_unbounded_residency",
                margin_ms=margin_ns / 1e6, ttl_ms=None if ttl_ns is None else ttl_ns / 1e6,
                changed=changed, ahead=ahead, behind=behind, empty=empty,
                repeats=repeats, stale_repeats=stale_repeats,
                signed_first_packet_age_reduction_ms=distribution(shifts),
                first_packet_to_synthetic_submit_ms=distribution(ages))


@dataclass(frozen=True)
class Eye:
    frame: int
    warp: int
    eye: int
    ready: int
    deadline: int
    width: int = 576
    height: int = 640
    kind: str = "vr444"

    @property
    def identity(self):
        return self.frame, self.warp, self.width, self.height, self.kind


class DeadlinePairs:
    """Synthetic bounded publication policy, NOT the production admission class.

    At most two pending frame IDs; preserve newest complete against orphans.
    Deadline is caller-supplied and shared by eyes. Never publish one eye,
    a future completion, a mismatched identity, or a frame at/below retirement.
    No active GPU model: this operates after externally supplied completions.
    """
    def __init__(self):
        self.pending = {}
        self.retired = -1

    def offer(self, eye):
        for name in ("frame", "warp", "ready", "deadline"):
            integer(getattr(eye, name), name)
        if type(eye.eye) is not int or eye.eye not in (0, 1):
            raise ValueError("invalid eye")
        integer(eye.width, "width", 1)
        integer(eye.height, "height", 1)
        if eye.frame <= self.retired:
            return False
        pair = self.pending.setdefault(eye.frame, {})
        if eye.eye in pair:
            return False
        if pair and any(other.identity != eye.identity or other.deadline != eye.deadline
                        for other in pair.values()):
            self.retire(eye.frame)
            return False
        pair[eye.eye] = eye
        while len(self.pending) > 2:
            complete = [f for f, p in self.pending.items() if len(p) == 2]
            protected = max(complete) if complete else None
            victim = min(f for f in self.pending if f != protected)
            del self.pending[victim]
        return True

    def retire(self, frame):
        self.retired = max(self.retired, frame)
        self.pending = {f: p for f, p in self.pending.items() if f > self.retired}

    def select(self, now):
        integer(now, "now")
        candidates = [f for f, p in self.pending.items() if len(p) == 2 and
                      all(e.ready <= now <= e.deadline for e in p.values())]
        if not candidates:
            return None
        chosen = max(candidates)
        pair = self.pending[chosen]
        self.retire(chosen)
        return pair[0], pair[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(run):
    manifest = json.loads((run / "hashes.json").read_text())
    paths = [run / "client/codec-test-present.jsonl"] + sorted((run / "client/codec-test").glob("decode-*.jsonl"))
    evidence = []
    for path in paths:
        name = str(path.relative_to(run))
        value = digest(path)
        if manifest.get(name) != value:
            raise ValueError(f"manifest mismatch: {path}")
        evidence.append(dict(path=str(path), sha256=value, bytes=path.stat().st_size))
    pairs, events = parse_present([json.loads(x) for x in paths[0].read_text().splitlines() if x])
    decodes = {}
    for path in paths[1:]:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("failed"):
                raise ValueError("failed decoder row requires explicit treatment")
            if row["eye"] not in (0, 1):
                continue
            key = row["frame"], row["warp"], row["eye"]
            if key in decodes:
                raise ValueError("duplicate decoder identity")
            decodes[key] = row
    matched = [decodes[p.frame, p.warp, eye] for p in pairs for eye in (0, 1)]
    observed = {
        "first_packet_to_pair_published_ms": distribution((p.complete - min(p.receive)) / 1e6 for p in pairs),
        "eye_publication_skew_ms": distribution(abs(p.ready[0] - p.ready[1]) / 1e6 for p in pairs),
        "pair_published_to_submit_ms": distribution((s - p.complete) / 1e6 for s, _, p in events),
        "first_packet_to_submit_ms": distribution((s - min(p.receive)) / 1e6 for s, _, p in events),
        "predicted_display_minus_submit_ms": distribution((d - s) / 1e6 for s, d, _ in events),
        "submit_after_predicted_display_count": sum(s > d for s, d, _ in events),
        "frame_regressions": sum(b[2].frame < a[2].frame for a, b in zip(events, events[1:])),
        "consecutive_repeats": sum(b[2].identity == a[2].identity for a, b in zip(events, events[1:])),
    }
    for key in ("queue_wait_ms", "decode_upload_ms", "decode_gpu_ms", "decompression_ms"):
        observed["submitted_unique_eye_" + key] = distribution(r[key] for r in matched)
        observed["all_decoded_eye_" + key] = distribution(r[key] for r in decodes.values())
    gpu_pairs = [(decodes[p.frame, p.warp, 0]["decode_gpu_ms"],
                  decodes[p.frame, p.warp, 1]["decode_gpu_ms"]) for p in pairs]
    return dict(run=run.name, input_hashes=evidence, submitted_rows=len(events), unique_submitted_pairs=len(pairs),
                decoded_eyes=len(decodes), decoded_eyes_without_absolute_publication_time=len(decodes)-len(matched),
                measured_observations=observed,
                assumed_fixed_gpu_costs_not_speedup={
                    "ideal_overlap_lower_envelope_ms": distribution(max(a, b) for a, b in gpu_pairs),
                    "serial_sum_envelope_ms": distribution(a+b for a, b in gpu_pairs),
                    "warning": "Recorded per-eye spans include GPU scheduling effects; these arithmetic envelopes are not observed pair makespan or achievable overlap."},
                counterfactual=[frontier(pairs, events, ms*1000000, ttl) for ms in (0, 1, 2, 4)
                                for ttl in (None, 20_000_000, 33_333_333)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.root / "validation/results/native-codec-comparison-0.12.0"
    result = dict(schema=1, status="offline_analysis_only", measured_speedup=None,
                  limitations=["Only submitted frames have absolute receive/publication XR timestamps.",
                    "Receive is first packet, not complete payload or source render time.",
                    "Decode-ready means CPU publication after logging/capture, not GPU completion.",
                    "Selector-call time, prior-fence wait and real three-entry residency are unknown.",
                    "Predicted display and xrEndFrame return are not photon measurements.",
                    "Frontier uses future-known identities only after recorded readiness but omits never-submitted candidates and actual residency.",
                    "Alternate batching/order/transport costs cannot be estimated causally from these logs."],
                  runs=[analyze(base / name) for name in ("04-custom", "05-custom")])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "runs": [{k: r[k] for k in ("run", "submitted_rows", "unique_submitted_pairs", "decoded_eyes")} for r in result["runs"]]}))


if __name__ == "__main__":
    main()
