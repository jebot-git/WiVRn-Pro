"""Strict streaming JSONL ingestion with disk-backed frame grouping.

One file = one run. Timestamps are integer monotonic nanoseconds. Display
prediction is explicitly separate from optical motion-to-photon measurement.
"""
import itertools
from contextlib import closing
import json
import math
import sqlite3
import tempfile
from pathlib import Path
from plans import THRESHOLDS, IMPLEMENTATIONS

STAGES = ("render_ready", "encode_start", "encode_end", "send_first", "send_last",
          "recv_first", "recv_last", "decode_start", "decode_end", "present_submit", "predicted_display")
EXTRA = ("frame_drop", "optical_motion", "optical_photon")
PAIRS = {"encode_ms": ("encode_start", "encode_end"),
         "decode_ms": ("decode_start", "decode_end"),
         "network_tail_ms": ("send_last", "recv_last"),
         "receive_to_decode_ms": ("recv_last", "decode_start"),
         "decode_to_present_ms": ("decode_end", "present_submit"),
         "encode_to_predicted_display_ms": ("encode_start", "predicted_display"),
         "render_to_predicted_display_ms": ("render_ready", "predicted_display"),
         "optical_motion_to_photon_ms": ("optical_motion", "optical_photon")}


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be integer >= {minimum}")
    return value


def normalize(ts, domain, meta):
    """Affine mapping around local anchors avoids large epoch float rounding.

    Mapping must come from measured clock sync. Anchors bound extrapolation;
    uncertainty is conservatively the max of its adjacent anchor bounds.
    No mapping => same-domain metrics only, never invented one-way latency.
    """
    if domain == meta["reference_clock"]:
        return ts, 0
    anchors = meta.get("clock_maps", {}).get(domain, [])
    if len(anchors) < 2:
        return None, None
    for a, b in zip(anchors, anchors[1:]):
        if b["local_ns"] <= a["local_ns"] or b["reference_ns"] <= a["reference_ns"]:
            raise ValueError("clock anchors must be strictly increasing")
    index = next((i for i in range(len(anchors)-1) if ts <= anchors[i+1]["local_ns"]), len(anchors)-2)
    a, b = anchors[index:index+2]
    age = max(a["local_ns"] - ts, ts - b["local_ns"], 0)
    if age > THRESHOLDS["max_clock_extrapolation_ns"]:
        return None, None
    uncertainty = max(a["uncertainty_ns"], b["uncertainty_ns"])
    if uncertainty > THRESHOLDS["max_clock_uncertainty_ns"]:
        return None, uncertainty
    local_delta = b["local_ns"] - a["local_ns"]
    reference_delta = b["reference_ns"] - a["reference_ns"]
    # Integer arithmetic avoids loss of precision at large monotonic epochs.
    return a["reference_ns"] + (ts-a["local_ns"]) * reference_delta // local_delta, uncertainty


def analyze(path):
    problems, incomplete, misses = [], [], []
    with tempfile.TemporaryDirectory(prefix="wivrn-trace-") as temp, closing(sqlite3.connect(Path(temp) / "events.sqlite")) as db:
        db.execute("CREATE TABLE events(frame INTEGER,eye INTEGER,stage TEXT,ts INTEGER,domain TEXT,norm INTEGER,body TEXT,PRIMARY KEY(frame,eye,stage))")
        db.execute("CREATE TABLE metrics(name TEXT,value REAL)")
        meta = None
        records = 0
        with open(path) as source:
            for line_num, line in enumerate(source, 1):
                if len(line) > 65536:
                    raise ValueError(f"oversized trace line {line_num}")
                row = json.loads(line)
                if meta is None:
                    if row.get("type") != "run" or row.get("schema") != 1:
                        raise ValueError("first record must be schema 1 run metadata")
                    meta = row
                    if meta["implementation"] not in IMPLEMENTATIONS or meta["provenance"] not in ("hardware", "software", "simulation"):
                        raise ValueError("invalid implementation/provenance")
                    count = integer(meta["expected_frames"], "expected_frames", 1)
                    warmup = integer(meta["warmup_frames"], "warmup_frames")
                    if warmup >= count or not math.isfinite(meta["fps"]) or not 0 < meta["fps"] <= 1000:
                        raise ValueError("invalid warmup/fps")
                    for anchors in meta.get("clock_maps", {}).values():
                        for anchor in anchors:
                            for key in ("local_ns", "reference_ns", "uncertainty_ns"):
                                integer(anchor[key], key)
                    for field in ("run_id", "case_id", "reference_clock"):
                        if not isinstance(meta[field], str) or not meta[field]:
                            raise ValueError(f"missing {field}")
                    integer(meta["telemetry_dropped"], "telemetry_dropped")
                    integer(meta["measurement_duration_ns"], "measurement_duration_ns", 1)
                    continue
                if row.get("type") != "event" or row.get("run_id") != meta["run_id"]:
                    raise ValueError(f"invalid record/run at line {line_num}")
                frame = integer(row["frame"], "frame")
                eye = integer(row["eye"], "eye")
                stage = row["stage"]
                ts = integer(row["ts_ns"], "ts_ns")
                if frame >= count or eye not in (0, 1) or stage not in STAGES + EXTRA:
                    raise ValueError(f"invalid frame/eye/stage at line {line_num}")
                if not isinstance(row["clock"], str) or not row["clock"]:
                    raise ValueError("event clock required")
                integer(row["warp_id"], "warp_id")
                norm, uncertainty = normalize(ts, row["clock"], meta)
                row["uncertainty_ns"] = uncertainty
                try:
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (frame, eye, stage, ts, row["clock"], norm, json.dumps(row)))
                except sqlite3.IntegrityError:
                    raise ValueError(f"duplicate frame/eye/stage at line {line_num}") from None
                records += 1
        if meta is None:
            raise ValueError("empty trace")
        db.commit()
        eligible = (count-warmup)*2
        observed = drops = complete = payload = wire = 0
        first_send, last_send = None, None
        displayed = {}
        uncertain_pairs = 0
        max_pair_uncertainty = 0
        for (frame, eye), group in itertools.groupby(db.execute("SELECT frame,eye,stage,ts,domain,norm,body FROM events ORDER BY frame,eye"), key=lambda r:r[:2]):
            events = {r[2]: dict(ts=r[3], clock=r[4], norm=r[5], body=json.loads(r[6])) for r in group}
            if len({e["body"]["warp_id"] for e in events.values()}) != 1:
                problems.append(f"warp metadata changed within frame {frame} eye {eye}")
            if "predicted_display" in events:
                p = events["predicted_display"]["body"]
                if p.get("paired_frame") != frame or p.get("applied_warp_id") != p["warp_id"]:
                    problems.append(f"stereo/warp mismatch frame {frame} eye {eye}")
                displayed[(frame, eye)] = p.get("pair_id")
            if frame < warmup:
                continue
            observed += 1
            if "send_last" in events:
                e = events["send_last"]["body"]
                payload += integer(e.get("payload_bytes"), "payload_bytes")
                w = integer(e.get("wire_bytes"), "wire_bytes")
                if w < e["payload_bytes"]:
                    problems.append("wire bytes smaller than payload")
                wire += w
            if "frame_drop" in events:
                drops += 1
                if "predicted_display" in events or not events["frame_drop"]["body"].get("reason"):
                    problems.append(f"invalid drop frame {frame} eye {eye}")
                continue
            missing = set(STAGES)-events.keys()
            if missing:
                incomplete.append(f"frame {frame} eye {eye} missing {','.join(sorted(missing))}")
            else:
                complete += 1
            # Pipelined encode/send and receive/decode may overlap. Only enforce
            # causal pairs, not a falsely serial ordering of every stage.
            pairs = dict(PAIRS)
            pairs.update({"_encode_order": ("render_ready", "encode_start"),
                          "_send_order": ("send_first", "send_last"),
                          "_recv_order": ("recv_first", "recv_last"),
                          "_present_order": ("present_submit", "predicted_display")})
            for name, (a, b) in pairs.items():
                if a not in events or b not in events:
                    continue
                a, b = events[a], events[b]
                uncertainty = 0
                if a["clock"] == b["clock"]:
                    delta = b["ts"] - a["ts"]
                elif a["norm"] is not None and b["norm"] is not None:
                    delta = b["norm"] - a["norm"]
                    uncertainty = (a["body"]["uncertainty_ns"] or 0) + (b["body"]["uncertainty_ns"] or 0)
                else:
                    uncertain_pairs += 1
                    continue
                max_pair_uncertainty = max(max_pair_uncertainty, uncertainty)
                # Decoder feeding can overlap packet reception; keep signed wait.
                if delta < -uncertainty and name != "receive_to_decode_ms":
                    problems.append(f"negative {name} frame {frame} eye {eye}")
                if name == "optical_motion_to_photon_ms" and not meta.get("optical_calibration"):
                    incomplete.append("optical events lack calibration/provenance")
                    continue
                if not name.startswith("_"):
                    db.execute("INSERT INTO metrics VALUES (?,?)", (name, delta / 1e6))
        if observed != eligible:
            incomplete.append(f"{eligible-observed} eye-frames have no events")
        for (frame, eye), pair in displayed.items():
            if pair is None or displayed.get((frame, 1-eye)) != pair:
                problems.append(f"unpaired stereo display frame {frame} eye {eye}")
        if meta["telemetry_dropped"]:
            incomplete.append("telemetry queue lost records")
        if uncertain_pairs:
            incomplete.append(f"{uncertain_pairs} cross-clock durations unavailable")
        db.execute("CREATE INDEX metric_quantiles ON metrics(name,value)")
        metrics = {}
        for name, n in db.execute("SELECT name,count(*) FROM metrics GROUP BY name"):
            result = {"samples": n}
            for q in (50, 95, 99):
                result[f"p{q}"] = db.execute("SELECT value FROM metrics WHERE name=? ORDER BY value LIMIT 1 OFFSET ?", (name, max(0, math.ceil(n*q/100)-1))).fetchone()[0]
            result["p99_low_sample_count"] = n < 1000
            metrics[name] = result
        seconds = meta["measurement_duration_ns"]/1e9
        drop_fraction = drops/eligible
        for key in ("encode_ms", "decode_ms", "encode_to_predicted_display_ms"):
            if key not in metrics:
                incomplete.append(f"{key} not measured")
            elif metrics[key]["p95"] > THRESHOLDS[key+"_p95"]:
                misses.append(key+" p95")
        if drop_fraction > THRESHOLDS["drop_fraction"]:
            misses.append("drop fraction")
        if complete + drops != eligible:
            incomplete.append("not every expected frame has a complete trace or an explicit drop")
        hardware = meta["provenance"] == "hardware" and meta.get("target_verified") is True and meta.get("wivrn_integrated") is True
        if meta["provenance"] == "hardware" and not hardware:
            incomplete.append("hardware/WiVRn identity not verified")
        status = "invalid" if problems else "inconclusive" if incomplete else "target_miss" if misses else "pass" if hardware else "software_only"
        return dict(schema=1, run_id=meta["run_id"], case_id=meta["case_id"], implementation=meta["implementation"],
                    status=status, provenance=meta["provenance"], records=records, expected_eye_frames=eligible,
                    fps=meta["fps"], expected_frames=count, warmup_frames=warmup, measurement_duration_ns=meta["measurement_duration_ns"],
                    completed_eye_frames=complete, dropped_eye_frames=drops, drop_fraction=drop_fraction,
                    metrics=metrics, actual_payload_mbps=payload*8/seconds/1e6,
                    actual_wire_mbps=wire*8/seconds/1e6, wire_layer=meta.get("wire_layer", "unspecified"),
                    max_pair_clock_uncertainty_ns=max_pair_uncertainty,
                    optical_status="measured" if "optical_motion_to_photon_ms" in metrics else "not_measured",
                    problems=problems[:100], problem_count=len(problems), incomplete=incomplete[:100],
                    incomplete_count=len(incomplete), target_misses=misses,
                    thresholds_status="provisional; pipeline pass alone does not establish image quality or optical MTP")
