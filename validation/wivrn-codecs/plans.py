"""Deterministic, declarative hardware test plans. No measurements are simulated."""
import hashlib
import json
import random

IMPLEMENTATIONS = ("hevc-carrier", "vulkan-vr444")
NETWORKS = {
    "clean": dict(goodput_mbps=600, delay_ms=1, jitter_ms=0, loss=0, duplicate=0, reorder_ms=0),
    "wifi-jitter": dict(goodput_mbps=300, delay_ms=2, jitter_ms=4, loss=.001, duplicate=.001, reorder_ms=5),
    "congested": dict(goodput_mbps=80, delay_ms=3, jitter_ms=8, loss=.01, duplicate=.002, reorder_ms=12),
    "burst-loss": dict(goodput_mbps=300, delay_ms=2, jitter_ms=1, loss=0, duplicate=0, reorder_ms=0,
                       burst_period_ms=1000, burst_duration_ms=30),
}
THRESHOLDS = {"encode_ms_p95": 4.0, "decode_ms_p95": 4.0,
              "encode_to_predicted_display_ms_p95": 30.0, "drop_fraction": .01,
              "protected_rgb_psnr_db_min": 40.0, "max_clock_uncertainty_ns": 500000,
              "max_clock_extrapolation_ns": 1000000000}
OPTICAL = dict(direction="motion-to-photon", minimum_trials_per_stratum=10,
               max_sample_gap_ns=1000000, max_uncertainty_ms=2.0, p95_limit_ms=50.0,
               hold_samples=3, motion_threshold=.5, light_threshold=.5, duration_ns=120000000000)
OPTICAL["trial_schedule"] = [dict(trial_id=f"e{eye}-{position}-{i:02d}",eye=eye,position=position)
                             for i in range(10) for eye in (0,1) for position in ("top","centre","bottom")]
for index, trial in enumerate(OPTICAL["trial_schedule"]):
    trial["offset_ns"] = index*2000000000


def make_plan(tier="full", seed=444):
    if tier not in ("smoke", "full", "soak", "optical"):
        raise ValueError("tier must be smoke, full, soak or optical")
    cases = []

    def add(impl, **overrides):
        case = dict(implementation=impl, fps=90, eye_size=[576, 640],
                    source_eye_size=[2304, 2560], carrier_size=[1152, 1920] if impl == "hevc-carrier" else None,
                    wivrn_foveation_setting=.75, dynamic_foveation=True,
                    rate_mbps=100, rate_mode="CBR" if impl == "hevc-carrier" else "protected-budget",
                    quality=None if impl == "hevc-carrier" else 8, kernel=None if impl == "hevc-carrier" else "coop128",
                    scene="colour-text", gaze="pursuit", network="clean", workload="vr-compositor",
                    lifecycle="steady", duration_s=30, warmup_s=5, repetitions=3,
                    measurement="pipeline", requires_capture_corpus=False)
        case.update(overrides)
        ident = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()[:16]
        case["case_id"] = impl + "-" + ident
        if not any(c["case_id"] == case["case_id"] for c in cases):
            cases.append(case)

    for impl in IMPLEMENTATIONS:
        if tier in ("full", "optical"):
            for rate in (50,100,150):
                for response_path in ("scene-update", "reprojected-view"):
                    add(impl, rate_mbps=rate, measurement="optical", duration_s=120, repetitions=1,
                        optical=dict(OPTICAL, response_path=response_path), scene="optical-motion-step")
            if tier == "optical":
                continue
        if tier == "smoke":
            for rate in (50, 100, 150):
                add(impl, rate_mbps=rate, duration_s=10, warmup_s=2, repetitions=1)
            continue
        if tier == "soak":
            for workload in ("idle-scene", "vr-compositor"):
                for rate in (50, 100, 150):
                    add(impl, workload=workload, rate_mbps=rate, duration_s=1800, warmup_s=60, repetitions=1)
            continue
        # Core crossed matrix: rates x content x gaze x wireless conditions.
        for rate in (50, 100, 150):
            for scene in ("rgb-ramp", "colour-text", "moving-edges", "cut-noise", "vr-capture"):
                for gaze in ("fixation", "pursuit", "saccade", "asymmetric"):
                    for network in ("clean", "wifi-jitter"):
                        add(impl, rate_mbps=rate, scene=scene, gaze=gaze, network=network,
                            requires_capture_corpus=scene == "vr-capture")
        # Quality modes have no bitrate target; measure resulting rate at each Q.
        if impl == "hevc-carrier":
            for mode in ("ICQ", "CQP"):
                for q in (18, 24, 30):
                    for scene in ("colour-text", "cut-noise", "vr-capture"):
                        add(impl, rate_mode=mode, quality=q, rate_mbps=None, scene=scene,
                            requires_capture_corpus=scene == "vr-capture")
        else:
            for kernel in ("baseline", "coop64", "coop128"):
                for q in (4, 8, 16):
                    for rate in (50, 100, 150):
                        add(impl, kernel=kernel, quality=q, rate_mbps=rate)
        for gaze in ("blink-dropout", "stale", "outlier", "timestamp-reset"):
            add(impl, gaze=gaze)
        for network in ("congested", "burst-loss"):
            add(impl, network=network, measurement="recovery")
        for lifecycle in ("reconnect", "resize", "eye-tracking-lost", "surface-recreate", "focus-loss", "codec-fallback"):
            add(impl, lifecycle=lifecycle, measurement="lifecycle")
    random.Random(seed).shuffle(cases)
    seconds = sum((c["duration_s"] + c["warmup_s"]) * c["repetitions"] for c in cases)
    return dict(schema=1, tier=tier, seed=seed, targets=dict(host="Intel Arc A770", client="Meta Quest Pro / XR2+ Gen 1"),
                foveation_note="75% is a WiVRn setting, not a bandwidth fraction. Record actual per-eye warp/footprints.",
                thresholds=THRESHOLDS, thresholds_status="provisional engineering targets, not perceptual acceptance",
                network_profiles=NETWORKS, cases=cases, case_count=len(cases),
                run_count=sum(c["repetitions"] for c in cases), estimated_acquisition_seconds=seconds,
                estimate_excludes="setup, recovery, cooldown, failed/repeated runs and adapter overhead")
