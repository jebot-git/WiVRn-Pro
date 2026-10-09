import copy
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from optical import analyze_optical
from plans import OPTICAL, make_plan
from runner import readiness


def fixture(delay_ms=20):
    config=copy.deepcopy(OPTICAL)
    calibration=dict(schema=1,calibration_id="synthetic-cal",provenance="simulation",common_clock=True,
                     physical_motion_sensor=True,physical_light_sensor=True,instrument="synthetic test only",
                     motion_sensor="simulated encoder",light_sensor="simulated photodiode",normalization="unit steps",
                     method="synthetic zero skew",raw_evidence_sha256="0"*64,
                     light_minus_motion_delay_ns=0,uncertainty_ns=100000,valid_from_ns=0,valid_until_ns=120000000000)
    trials=[]
    for i,spec in enumerate(config["trial_schedule"]):
        start=spec["offset_ns"]
        samples=[dict(t_ns=start+j*1000000,motion=int(j>=5),light=int(j>=5+delay_ms)) for j in range(80)]
        trials.append(dict(spec,status="captured",response_verified=True,samples=samples))
    capture=dict(schema=1,implementation="vulkan-vr444",run_id="synthetic",case_id="synthetic-case",
                 response_path="scene-update",instrument_clock="rig",calibration_id="synthetic-cal",provenance="simulation",
                 measurement_start_ns=0,measurement_end_ns=config["duration_ns"],
                 scheduled_trials=copy.deepcopy(config["trial_schedule"]),trials=trials)
    return capture,calibration,config


class OpticalTests(unittest.TestCase):
    def test_known_delay_uncertainty_and_strata(self):
        result=analyze_optical(*fixture())
        self.assertEqual(result["status"],"software_only")
        self.assertEqual(result["valid_trials"],60)
        for stat in result["strata"].values():
            self.assertEqual(stat["p50_ms"],20)
            self.assertAlmostEqual(stat["p95_upper_ms"],21.1)
            self.assertTrue(stat["p99_low_sample_count"])

    def test_missing_photon_cannot_pass(self):
        capture,cal,config=fixture()
        for s in capture["trials"][0]["samples"]: s["light"]=0
        result=analyze_optical(capture,cal,config)
        self.assertEqual(result["status"],"inconclusive")
        self.assertEqual(len(result["failed_trials"]),1)

    def test_missing_trial_and_cherry_picked_schedule(self):
        capture,cal,config=fixture(); capture["trials"].pop()
        self.assertEqual(analyze_optical(capture,cal,config)["status"],"inconclusive")
        capture["scheduled_trials"].pop()
        with self.assertRaisesRegex(ValueError,"predeclared"): analyze_optical(capture,cal,config)

    def test_duplicate_trial(self):
        capture,cal,config=fixture(); capture["trials"].append(capture["trials"][0])
        with self.assertRaisesRegex(ValueError,"duplicate"): analyze_optical(capture,cal,config)

    def test_trial_timing_cannot_be_cherry_picked(self):
        capture,cal,config=fixture(); capture["measurement_end_ns"]-=1
        with self.assertRaisesRegex(ValueError,"duration"): analyze_optical(capture,cal,config)
        capture,cal,config=fixture()
        for sample in capture["trials"][1]["samples"]: sample["t_ns"]-=2000000000
        self.assertEqual(analyze_optical(capture,cal,config)["status"],"inconclusive")

    def test_noise_and_insufficient_baseline(self):
        for i in (0,10):
            capture,cal,config=fixture(); capture["trials"][0]["samples"][i]["light"]=1
            self.assertEqual(analyze_optical(capture,cal,config)["status"],"inconclusive")

    def test_clock_gaps_expiry_and_nonfinite(self):
        for kind in ("gap","expiry","nan","unsynced"):
            capture,cal,config=fixture()
            if kind=="gap": capture["trials"][0]["samples"][10]["t_ns"]+=3000000
            elif kind=="expiry": cal["valid_until_ns"]=100000000
            elif kind=="nan": capture["trials"][0]["samples"][10]["light"]=float("nan")
            else:
                cal["common_clock"]=False
                with self.assertRaises(ValueError): analyze_optical(capture,cal,config)
                continue
            self.assertEqual(analyze_optical(capture,cal,config)["status"],"inconclusive")

    def test_channel_delay_correction(self):
        capture,cal,config=fixture(); cal["light_minus_motion_delay_ns"]=2000000
        self.assertEqual(analyze_optical(capture,cal,config)["strata"]["eye0-top"]["p50_ms"],18)

    def test_excessive_uncertainty_and_reversed_causality(self):
        capture,cal,config=fixture(); cal["uncertainty_ns"]=3000000
        self.assertEqual(analyze_optical(capture,cal,config)["status"],"inconclusive")
        capture,cal,config=fixture(); cal["light_minus_motion_delay_ns"]=25000000
        self.assertEqual(analyze_optical(capture,cal,config)["status"],"inconclusive")

    def test_conservative_limit_and_wrong_direction(self):
        capture,cal,config=fixture(50)
        self.assertEqual(analyze_optical(capture,cal,config)["status"],"target_miss")
        config["direction"]="photon-to-motion"
        with self.assertRaises(ValueError): analyze_optical(capture,cal,config)

    def test_provenance_requires_both_capture_and_calibration(self):
        capture,cal,config=fixture(); capture["provenance"]="hardware"
        self.assertEqual(analyze_optical(capture,cal,config)["status"],"software_only")

    def test_optical_plan_coverage_and_readiness(self):
        plan=make_plan("optical")
        self.assertEqual(plan["case_count"],12)
        self.assertEqual(plan["estimated_acquisition_seconds"],1500)
        self.assertEqual(len({(c["implementation"],c["rate_mbps"],c["optical"]["response_path"]) for c in plan["cases"]}),12)
        self.assertIn("calibrated physical motion/light rig missing",readiness({},plan["cases"]))


if __name__=="__main__": unittest.main()
