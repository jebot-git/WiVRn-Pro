"""Physical motion-to-photon analysis from synchronized instrument samples.

This does not infer photon times from OpenXR predictions or software traces.
Input channels must be calibrated normalized physical motion and light signals.
"""
import math
from plans import IMPLEMENTATIONS

POSITIONS = ("top", "centre", "bottom")
PATHS = ("scene-update", "reprojected-view")


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name+" must be finite numeric data")
    return value


def percentile(values, q):
    return sorted(values)[max(0, math.ceil(len(values)*q/100)-1)]


def crossing(samples, channel, threshold, hold):
    """Bound onset between last below-threshold and first sustained high sample.

    Reject missing baselines and early noise spikes instead of choosing a later,
    more favourable edge. No interpolation creates fictitious sample precision.
    """
    if any(s[channel] >= threshold for s in samples[:hold]):
        raise ValueError(channel+" lacks a stable pre-trigger baseline")
    for index in range(hold, len(samples)):
        if samples[index][channel] < threshold:
            continue
        if index+hold > len(samples) or any(s[channel] < threshold for s in samples[index:index+hold]):
            raise ValueError(channel+" has an ambiguous/unstable first crossing")
        return samples[index-1]["t_ns"], samples[index]["t_ns"]
    raise ValueError(channel+" response missing")


def analyze_optical(capture, calibration, config):
    if config.get("direction") != "motion-to-photon":
        raise ValueError("this test measures physical motion followed by display photons")
    if capture.get("schema") != 1 or calibration.get("schema") != 1:
        raise ValueError("optical schema 1 required")
    if capture.get("implementation") not in IMPLEMENTATIONS or capture.get("response_path") not in PATHS:
        raise ValueError("invalid optical implementation/response path")
    for key in ("run_id", "case_id", "instrument_clock", "calibration_id"):
        if not isinstance(capture.get(key),str) or not capture[key]:
            raise ValueError("missing optical "+key)
    if capture["calibration_id"] != calibration.get("calibration_id"):
        raise ValueError("optical calibration ID mismatch")
    if calibration.get("common_clock") is not True or calibration.get("physical_motion_sensor") is not True or calibration.get("physical_light_sensor") is not True:
        raise ValueError("both physical channels on a common instrument clock are required")
    for key in ("instrument", "motion_sensor", "light_sensor", "normalization", "method", "raw_evidence_sha256"):
        if not calibration.get(key):
            raise ValueError("missing optical calibration "+key)
    # Positive correction means the light channel has more acquisition delay.
    correction = number(calibration["light_minus_motion_delay_ns"],"channel delay")
    uncertainty = number(calibration["uncertainty_ns"],"calibration uncertainty")
    if uncertainty < 0:
        raise ValueError("negative calibration uncertainty")
    first = calibration["valid_from_ns"]; last = calibration["valid_until_ns"]
    if type(first) is not int or type(last) is not int or first < 0 or last <= first:
        raise ValueError("invalid calibration validity interval")
    max_gap = number(config["max_sample_gap_ns"],"max sample gap")
    max_uncertainty = number(config["max_uncertainty_ms"],"max uncertainty")
    ceiling = number(config["p95_limit_ms"],"p95 limit")
    minimum = config["minimum_trials_per_stratum"]
    hold = config["hold_samples"]
    if max_gap <= 0 or max_uncertainty <= 0 or ceiling <= 0 or type(minimum) is not int or minimum < 1 or type(hold) is not int or hold < 2:
        raise ValueError("invalid optical acquisition/acceptance settings")
    thresholds = {key:number(config[key+"_threshold"],key+" threshold") for key in ("motion","light")}
    if any(not 0 < t < 1 for t in thresholds.values()):
        raise ValueError("normalized thresholds must be inside (0,1)")
    schedule = capture["scheduled_trials"]
    if schedule != config["trial_schedule"]:
        raise ValueError("optical schedule differs from the predeclared test plan")
    start=capture["measurement_start_ns"]; end=capture["measurement_end_ns"]
    if type(start) is not int or type(end) is not int or start < 0 or end-start != config["duration_ns"]:
        raise ValueError("optical measurement window differs from the planned duration")
    ids = [s["trial_id"] for s in schedule]
    if not ids or len(set(ids)) != len(ids) or any(not isinstance(i,str) or not i for i in ids):
        raise ValueError("unique nonempty scheduled optical trial IDs required")
    scheduled = {s["trial_id"]:s for s in schedule}
    for s in schedule:
        if type(s["eye"]) is not int or s["eye"] not in (0,1) or s["position"] not in POSITIONS:
            raise ValueError("invalid scheduled eye/scanout position")
    offsets=[s["offset_ns"] for s in schedule]
    if any(type(t) is not int or not 0 <= t < end-start for t in offsets) or any(b<=a for a,b in zip(offsets,offsets[1:])):
        raise ValueError("invalid optical schedule offsets")
    slots={s["trial_id"]:(start+s["offset_ns"],start+offsets[i+1] if i+1<len(offsets) else end) for i,s in enumerate(schedule)}
    seen=set(); results=[]; failures=[]
    for trial in capture["trials"]:
        tid=trial["trial_id"]
        if tid not in scheduled or tid in seen:
            raise ValueError("unscheduled or duplicate optical trial")
        seen.add(tid); spec=scheduled[tid]
        if trial.get("eye") != spec["eye"] or trial.get("position") != spec["position"]:
            raise ValueError("optical trial does not match its scheduled stratum")
        try:
            if trial.get("status") != "captured":
                raise ValueError("trial not captured: "+str(trial.get("reason","unspecified")))
            if trial.get("response_verified") is not True:
                raise ValueError("motion stimulus and displayed response association unverified")
            samples=trial["samples"]
            if len(samples) < 2*hold+1:
                raise ValueError("too few optical samples")
            previous=None
            for sample in samples:
                ts=sample["t_ns"]
                if type(ts) is not int or ts < first or ts > last:
                    raise ValueError("sample outside calibration validity interval")
                if not slots[tid][0] <= ts < slots[tid][1]:
                    raise ValueError("sample outside scheduled trial window")
                if previous is not None and not 0 < ts-previous <= max_gap:
                    raise ValueError("non-monotonic or gapped instrument samples")
                previous=ts
                for channel in ("motion","light"):
                    if not 0 <= number(sample[channel],channel) <= 1:
                        raise ValueError("channel outside calibrated normalized range")
            motion=crossing(samples,"motion",thresholds["motion"],hold)
            light=crossing(samples,"light",thresholds["light"],hold)
            lower=(light[0]-motion[1]-correction-uncertainty)/1e6
            upper=(light[1]-motion[0]-correction+uncertainty)/1e6
            if lower < 0:
                raise ValueError("response precedes motion or uncertainty overlaps zero")
            if (upper-lower)/2 > max_uncertainty:
                raise ValueError("optical uncertainty exceeds acquisition limit")
            results.append(dict(trial_id=tid,eye=spec["eye"],position=spec["position"],
                                latency_ms=(lower+upper)/2,lower_ms=lower,upper_ms=upper))
        except (ValueError,KeyError) as error:
            failures.append(dict(trial_id=tid,reason=str(error)))
    failures.extend(dict(trial_id=tid,reason="scheduled trial missing") for tid in ids if tid not in seen)
    summaries={}; incomplete=[]; misses=[]
    for eye in (0,1):
        for position in POSITIONS:
            name=f"eye{eye}-{position}"
            group=[r for r in results if r["eye"]==eye and r["position"]==position]
            summary=dict(samples=len(group),p99_low_sample_count=len(group)<1000)
            if len(group)<minimum:
                incomplete.append(name+" has insufficient trials")
            if group:
                for q in (50,95,99):
                    summary[f"p{q}_ms"]=percentile([r["latency_ms"] for r in group],q)
                summary["p95_upper_ms"]=percentile([r["upper_ms"] for r in group],95)
                summary["max_uncertainty_ms"]=max((r["upper_ms"]-r["lower_ms"])/2 for r in group)
                if summary["p95_upper_ms"]>ceiling:
                    misses.append(name+" p95 upper bound exceeds target")
            summaries[name]=summary
    hardware=capture.get("provenance")=="hardware" and calibration.get("provenance")=="hardware"
    status="inconclusive" if failures or incomplete else "target_miss" if misses else "pass" if hardware else "software_only"
    return dict(schema=1,run_id=capture["run_id"],case_id=capture["case_id"],implementation=capture["implementation"],
                metric="physical_motion_to_photon",response_path=capture["response_path"],status=status,
                scheduled_trials=len(ids),valid_trials=len(results),failed_trials=failures,coverage_issues=incomplete,
                strata=summaries,trials=results,target_misses=misses,settings=config,
                acceptance_basis="provisional engineering target; p95 conservative upper bound for every eye/scanout stratum")
