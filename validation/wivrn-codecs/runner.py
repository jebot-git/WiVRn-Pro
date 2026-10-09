"""Explicit host/client adapter protocol; never changes a live service itself."""
import hashlib
import json
import math
import subprocess
from pathlib import Path
from trace import analyze
from optical import analyze_optical


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def call(adapter, action, request, folder, timeout):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    request_path = folder / (action+"-request.json")
    response_path = folder / (action+"-response.json")
    save(request_path, request)
    with open(folder/(action+"-stdout.log"),"wb") as stdout, open(folder/(action+"-stderr.log"),"wb") as stderr:
        subprocess.run([str(Path(adapter).resolve()), action, "--request", str(request_path),
                        "--response", str(response_path)], stdout=stdout, stderr=stderr, timeout=timeout, check=True)
    return json.loads(response_path.read_text())


def readiness(probe, cases):
    blockers = []
    if probe.get("schema") != 1:
        blockers.append("adapter schema 1 required")
    for side, model in (("host","Intel Arc A770"),("client","Meta Quest Pro")):
        data = probe.get(side, {})
        if data.get("model") != model or data.get("verified") is not True or not data.get("evidence"):
            blockers.append(side+" target identity/evidence missing")
    required = {"dynamic_foveation", "atomic_frame_warp", "stereo_frame_ids", "clock_sync", "bounded_telemetry", "quality_capture", "network_bytes", "runtime_provenance"}
    if probe.get("provenance") != "hardware":
        blockers.append("hardware provenance required")
    for impl in {c["implementation"] for c in cases}:
        cap = probe.get("implementations",{}).get(impl,{})
        if cap.get("wivrn_integrated") is not True:
            blockers.append(impl+" WiVRn host/client integration absent")
        blockers.extend(impl+" missing "+flag for flag in sorted(required) if cap.get(flag) is not True)
        for case in cases:
            if case["implementation"] != impl:
                continue
            for field, supported in (("rate_mode","rate_modes"),("kernel","kernels"),("gaze","gaze_patterns"),("network","network_profiles"),("lifecycle","lifecycle_tests"),("measurement","measurements")):
                if case[field] is not None and case[field] not in cap.get(supported,[]):
                    blockers.append(f"{impl} unsupported {field}={case[field]}")
            if case["requires_capture_corpus"] and not probe.get("capture_corpus_sha256"):
                blockers.append("hashed VR capture corpus missing")
            if case["measurement"] == "optical":
                rig=probe.get("optical_rig",{})
                if rig.get("ready") is not True or not rig.get("calibration_sha256") or rig.get("physical_motion_and_light") is not True:
                    blockers.append("calibrated physical motion/light rig missing")
                if case["optical"]["response_path"] not in cap.get("optical_response_paths",[]):
                    blockers.append(impl+" optical response path unsupported")
    return sorted(set(blockers))


def artifact(folder, descriptor):
    path = (Path(folder) / descriptor["path"]).resolve()
    if not path.is_relative_to(Path(folder).resolve()) or not path.is_file():
        raise ValueError("adapter artifact escapes run folder or does not exist")
    with path.open("rb") as source:
        sha = hashlib.file_digest(source,"sha256").hexdigest()
    if sha != descriptor["sha256"]:
        raise ValueError("adapter artifact checksum mismatch")
    return path


def run(adapter, plan, output, case_ids=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    save(output/"plan.json", plan)
    cases = [c for c in plan["cases"] if not case_ids or c["case_id"] in case_ids]
    if not cases or (case_ids and set(case_ids)-{c["case_id"] for c in cases}):
        raise ValueError("unknown or empty case selection")
    probe = call(adapter,"probe",dict(schema=1, cases=cases, output_dir=str(output)),output,60)
    blockers = readiness(probe,cases)
    save(output/"readiness.json",dict(ready=not blockers, blockers=blockers))
    if blockers:
        return dict(status="blocked", blockers=blockers, completed_runs=0)
    results = []
    try:
        for case in cases:
            for rep in range(case["repetitions"]):
                run_id = f'{case["case_id"]}-r{rep+1}'
                folder = output/run_id
                request = dict(schema=1, action="run", run_id=run_id, case=case, network=plan["network_profiles"][case["network"]],
                               seed=plan["seed"]+rep, output_dir=str(folder), isolation="dedicated test instance; restore captured state on stop")
                response = call(adapter,"run",request,folder,case["duration_s"]+case["warmup_s"]+180)
                if response.get("run_id") != run_id or response.get("status") != "completed":
                    raise ValueError("adapter run identity/completion mismatch")
                trace_path = artifact(folder,response["trace"])
                report = analyze(trace_path)
                if report["run_id"] != run_id or report["case_id"] != case["case_id"] or report["implementation"] != case["implementation"]:
                    raise ValueError("trace identity differs from case")
                quality = json.loads(artifact(folder,response["quality"]).read_text())
                # Require empirical results for both geometry loss and codec loss.
                reasons = []
                expected = round((case["duration_s"]+case["warmup_s"])*case["fps"])
                if report["fps"] != case["fps"] or report["expected_frames"] != expected or report["warmup_frames"] != round(case["warmup_s"]*case["fps"]):
                    reasons.append("trace frame coverage differs from requested acquisition")
                if abs(report["measurement_duration_ns"]/1e9-case["duration_s"]) > 1/case["fps"]:
                    reasons.append("trace duration differs from requested acquisition")
                if case["rate_mbps"] is not None and report["actual_payload_mbps"] > case["rate_mbps"]*1.1:
                    reasons.append("payload rate exceeds target by more than provisional 10% allowance")
                for field in ("codec_only", "source_to_presented"):
                    q = quality.get(field,{}).get("protected",{})
                    if q.get("measured") is not True or q.get("pixels",0) <= 0:
                        reasons.append(field+" protected image quality unmeasured")
                    elif q.get("exact") is not True and (not isinstance(q.get("psnr_db"),(float,int)) or not math.isfinite(q["psnr_db"]) or q["psnr_db"] < plan["thresholds"]["protected_rgb_psnr_db_min"]):
                        reasons.append(field+" protected image quality below provisional target")
                if case["measurement"] == "optical":
                    if any(key not in response for key in ("optical_capture","optical_calibration","optical_calibration_raw")):
                        reasons.append("physical motion-to-photon capture/calibration missing")
                    else:
                        capture=json.loads(artifact(folder,response["optical_capture"]).read_text())
                        calibration=json.loads(artifact(folder,response["optical_calibration"]).read_text())
                        artifact(folder,response["optical_calibration_raw"])
                        if calibration.get("raw_evidence_sha256") != response["optical_calibration_raw"]["sha256"]:
                            raise ValueError("raw optical calibration evidence hash mismatch")
                        optical=analyze_optical(capture,calibration,case["optical"])
                        if optical["run_id"] != run_id or optical["case_id"] != case["case_id"] or optical["implementation"] != case["implementation"] or optical["response_path"] != case["optical"]["response_path"]:
                            raise ValueError("optical capture identity differs from requested case")
                        report["physical_motion_to_photon"]=optical
                        save(folder/"optical-report.json",optical)
                        if optical["status"] != "pass":
                            reasons.append("physical motion-to-photon "+optical["status"])
                checks = json.loads(artifact(folder,response["checks"]).read_text())
                for flag in ("gaze_fallback", "warp_alignment", "stereo_coherence", "resource_bounds", "colour_range", "thermal_telemetry", "requested_settings_applied", "network_profile_verified"):
                    if checks.get(flag) is not True:
                        reasons.append(flag+" not verified")
                if case["measurement"] in ("recovery","lifecycle") and checks.get(case["measurement"]+"_verified") is not True:
                    reasons.append(case["measurement"]+" outcome not verified")
                report["suite_issues"] = reasons
                report["suite_status"] = "pass" if report["status"] == "pass" and not reasons else "not_accepted"
                save(folder/"report.json",report)
                results.append(report)
    finally:
        # Called even after timeout/error. Adapter owns cleanup of its test
        # process group and restoration, including remote client state.
        cleanup = call(adapter,"stop",dict(schema=1, output_dir=str(output)),output,60)
        if cleanup.get("restored") is not True:
            raise RuntimeError("adapter did not confirm restoration; inspect stop response")
    summary = dict(status="pass" if all(r["suite_status"]=="pass" for r in results) else "not_accepted", completed_runs=len(results),
                   runs=[dict(run_id=r["run_id"],status=r["suite_status"]) for r in results])
    save(output/"summary.json",summary)
    return summary
