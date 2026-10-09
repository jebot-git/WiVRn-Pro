import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np
from plans import make_plan, NETWORKS
from trace import analyze, normalize, STAGES
from runner import readiness, artifact, run
from stimuli import rgb_frame, gaze_sample, quality
from impairment import schedule


def fixture(frames=3, provenance="simulation"):
    meta = dict(type="run",schema=1,run_id="test",case_id="case",implementation="vulkan-vr444",provenance=provenance,
                reference_clock="headset", expected_frames=frames,warmup_frames=1,fps=90,telemetry_dropped=0,
                measurement_duration_ns=round((frames-1)/90*1e9),wire_layer="IP+UDP+application",
                clock_maps={"host":[dict(local_ns=0,reference_ns=1000000000,uncertainty_ns=1000),dict(local_ns=1000000000,reference_ns=2000000000,uncertainty_ns=1000)]})
    events=[]
    for f in range(frames):
        for eye in (0,1):
            for s,stage in enumerate(STAGES):
                host=s<5
                event=dict(type="event",run_id="test",frame=f,eye=eye,stage=stage,warp_id=f*2+eye,clock="host" if host else "headset",ts_ns=f*12000000+s*1000000+(0 if host else 1000000000))
                if stage=="send_last": event.update(payload_bytes=1000,wire_bytes=1100)
                if stage=="predicted_display": event.update(paired_frame=f,pair_id=f,applied_warp_id=f*2+eye)
                events.append(event)
    return [meta]+events


def report(rows):
    with tempfile.TemporaryDirectory() as folder:
        p=pathlib.Path(folder)/"trace.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
        return analyze(p)


class TraceTests(unittest.TestCase):
    def test_complete_simulation_never_hardware_pass(self):
        r=report(fixture()); self.assertEqual(r["status"],"software_only")
        self.assertEqual(r["completed_eye_frames"],4)
        self.assertEqual(r["metrics"]["encode_ms"]["p95"],1)
        self.assertEqual(r["metrics"]["encode_to_predicted_display_ms"]["p95"],9)
        self.assertEqual(r["optical_status"],"not_measured")
        self.assertAlmostEqual(r["actual_payload_mbps"],1.44,places=6)

    def test_absent_frame_is_not_success(self):
        rows=fixture(); rows=[r for r in rows if r.get("frame")!=2]
        self.assertEqual(report(rows)["status"],"inconclusive")

    def test_duplicate_rejected(self):
        rows=fixture(); rows.append(rows[-1])
        with self.assertRaisesRegex(ValueError,"duplicate"): report(rows)

    def test_cross_clock_without_sync_inconclusive(self):
        rows=fixture(); rows[0]["clock_maps"]={}
        r=report(rows); self.assertEqual(r["status"],"inconclusive")
        self.assertEqual(r["metrics"]["encode_ms"]["p50"],1)
        self.assertNotIn("encode_to_predicted_display_ms",r["metrics"])

    def test_already_normalized_is_not_double_converted(self):
        rows=fixture()
        for row in rows[1:]:
            if row["clock"]=="host": row["ts_ns"]+=1000000000; row["clock"]="headset"
        self.assertEqual(report(rows)["metrics"]["encode_to_predicted_display_ms"]["p95"],9)

    def test_bad_clock_and_extrapolation(self):
        meta=fixture()[0]
        self.assertIsNone(normalize(4000000000,"host",meta)[0])
        for a in meta["clock_maps"]["host"]: a["uncertainty_ns"]=10000000
        self.assertIsNone(normalize(5,"host",meta)[0])
        meta["clock_maps"]["host"][1]["local_ns"]=0
        with self.assertRaises(ValueError): normalize(1,"host",meta)

    def test_negative_duration(self):
        rows=fixture()
        for r in rows[1:]:
            if r["frame"]==1 and r["stage"]=="encode_end": r["ts_ns"]=0
        self.assertEqual(report(rows)["status"],"invalid")

    def test_stale_warp_and_stereo(self):
        for field in ("applied_warp_id","paired_frame","pair_id","warp_id"):
            with self.subTest(field=field):
                rows=fixture(); rows[-1][field]=999
                self.assertEqual(report(rows)["status"],"invalid")

    def test_telemetry_loss(self):
        rows=fixture(); rows[0]["telemetry_dropped"]=1
        self.assertEqual(report(rows)["status"],"inconclusive")

    def test_explicit_drop_counts_transmitted_bytes(self):
        rows=fixture(); rows=[r for r in rows if not (r.get("frame")==2 and r.get("stage") in STAGES[5:])]
        for eye in (0,1): rows.append(dict(type="event",run_id="test",frame=2,eye=eye,stage="frame_drop",clock="headset",ts_ns=1040000000,warp_id=4+eye,reason="deadline"))
        r=report(rows); self.assertEqual(r["dropped_eye_frames"],2)
        self.assertEqual(r["status"],"target_miss")
        self.assertAlmostEqual(r["actual_payload_mbps"],1.44,places=6)

    def test_hardware_identity_required(self):
        self.assertEqual(report(fixture(provenance="hardware"))["status"],"inconclusive")

    def test_optical_requires_calibration(self):
        rows=fixture()
        for stage,ns in (("optical_motion",1000000000),("optical_photon",1020000000)):
            rows.append(dict(type="event",run_id="test",frame=1,eye=0,stage=stage,clock="headset",ts_ns=ns,warp_id=2))
        self.assertEqual(report(rows)["optical_status"],"not_measured")
        rows[0]["optical_calibration"]={"instrument":"test fixture only","latency_ns":0}
        self.assertEqual(report(rows)["metrics"]["optical_motion_to_photon_ms"]["p50"],20)


class PlanTests(unittest.TestCase):
    def test_reproducible_and_unique(self):
        p=make_plan(); self.assertEqual(p,make_plan())
        self.assertEqual(p["case_count"],len(set(c["case_id"] for c in p["cases"])))
        self.assertEqual(p["estimated_acquisition_seconds"],sum((c["duration_s"]+c["warmup_s"])*c["repetitions"] for c in p["cases"]))

    def test_quality_modes_do_not_claim_fixed_rate(self):
        cases=make_plan()["cases"]
        for mode in ("ICQ","CQP"):
            selected=[c for c in cases if c["rate_mode"]==mode]
            self.assertEqual(len(selected),9); self.assertTrue(all(c["rate_mbps"] is None for c in selected))

    def test_smoke_and_soak(self):
        self.assertEqual(make_plan("smoke")["case_count"],6)
        self.assertEqual(make_plan("soak")["case_count"],12)


class FixtureTests(unittest.TestCase):
    def test_reproducible_rgb_and_chroma_damage(self):
        for scene in ("rgb-ramp","colour-text","moving-edges","cut-noise"):
            a=rgb_frame(scene,32,32,4,0)
            self.assertTrue(np.array_equal(a,rgb_frame(scene,32,32,4,0)))
        a=rgb_frame("colour-text",32,32,4,0); zones=np.full((32,32),2)
        self.assertTrue(quality(a,a,zones)["protected"]["exact"])
        bad=a.copy(); bad[:,::2]=bad[:,1::2]
        self.assertGreater(quality(a,bad,zones)["protected"]["colour_difference_rmse"],100)

    def test_empty_zone_not_infinite_quality(self):
        a=rgb_frame("rgb-ramp",16,16,0,0)
        self.assertFalse(quality(a,a,np.zeros((16,16)))["protected"]["measured"])
        with self.assertRaises(ValueError): quality(a,a[:8],np.zeros((16,16)))

    def test_no_synthetic_substitution_for_vr_corpus(self):
        with self.assertRaises(ValueError): rgb_frame("vr-capture",16,16,0,0)

    def test_gaze_faults_and_stereo(self):
        self.assertFalse(gaze_sample("blink-dropout",0)["eyes"][0]["valid"])
        stale=gaze_sample("stale",100); self.assertEqual(stale["expected_consume_ns"]-stale["sample_ns"],100000000)
        self.assertGreater(gaze_sample("outlier",90)["eyes"][0]["angle_deg"][0],180)
        self.assertNotEqual(*[e["angle_deg"] for e in gaze_sample("asymmetric",0)["eyes"]])

    def test_impairment_repeatability_deadlines_and_queue(self):
        packets=[dict(id=i,sent_ns=i*10000,bytes=1200,deadline_ns=i*10000+20000000) for i in range(500)]
        a=schedule(packets,NETWORKS["wifi-jitter"])
        self.assertEqual(a,schedule(packets,NETWORKS["wifi-jitter"]))
        self.assertTrue(all(p["arrival_ns"]<packets[p["id"]]["deadline_ns"] for p in a["deliveries"]))
        self.assertTrue(schedule(packets,NETWORKS["congested"],queue_ms=0)["drops"])

    def test_burst_loss_and_invalid_packet(self):
        p=[dict(id=0,sent_ns=0,bytes=1200,deadline_ns=10000000)]
        self.assertEqual(schedule(p,NETWORKS["burst-loss"])["drops"][0]["reason"],"loss")
        p[0]["deadline_ns"]=0
        with self.assertRaises(ValueError): schedule(p,NETWORKS["clean"])


class AdapterTests(unittest.TestCase):
    def adapter_roundtrip(self, optical=False):
        plan=make_plan("smoke"); plan["cases"]=plan["cases"][:1]
        case=plan["cases"][0]; case.update(duration_s=1,warmup_s=1,repetitions=1)
        if optical:
            case["measurement"]="optical"
        actions=[]
        def fake(adapter,action,request,folder,timeout):
            actions.append(action)
            if action!="run": return dict(restored=True)
            folder=pathlib.Path(folder); folder.mkdir(parents=True)
            rows=fixture(180); rows[0].update(run_id=request["run_id"],case_id=case["case_id"],implementation=case["implementation"],warmup_frames=90,measurement_duration_ns=1000000000)
            rows[0]["clock_maps"]["host"][1].update(local_ns=10000000000,reference_ns=11000000000)
            if optical:
                rows[0]["optical_calibration"]={"instrument":"legacy simulated events"}
                for stage,ns in (("optical_motion",2000000000),("optical_photon",2020000000)):
                    rows.append(dict(type="event",run_id="test",frame=100,eye=0,stage=stage,clock="headset",ts_ns=ns,warp_id=200))
            for r in rows[1:]: r["run_id"]=request["run_id"]
            (folder/"trace.jsonl").write_text("\n".join(json.dumps(r) for r in rows)+"\n")
            quality={key:dict(protected=dict(pixels=10,measured=True,exact=True,psnr_db=None)) for key in ("codec_only","source_to_presented")}
            (folder/"quality.json").write_text(json.dumps(quality))
            checks={key:True for key in ("gaze_fallback","warp_alignment","stereo_coherence","resource_bounds","colour_range","thermal_telemetry","requested_settings_applied","network_profile_verified")}
            (folder/"checks.json").write_text(json.dumps(checks))
            response=dict(run_id=request["run_id"],status="completed")
            for key,filename in (("trace","trace.jsonl"),("quality","quality.json"),("checks","checks.json")):
                response[key]=dict(path=filename,sha256=hashlib.sha256((folder/filename).read_bytes()).hexdigest())
            return response
        with tempfile.TemporaryDirectory() as t, patch("runner.call",side_effect=fake), patch("runner.readiness",return_value=[]):
            result=run("fixture",plan,pathlib.Path(t)/"run")
            report=json.loads(next((pathlib.Path(t)/"run").glob("*/report.json")).read_text())
        self.assertEqual(result["status"],"not_accepted")
        self.assertEqual(result["completed_runs"],1)
        self.assertEqual(actions,["probe","run","stop"])
        return report

    def test_complete_adapter_roundtrip_still_rejects_simulated_hardware(self):
        self.adapter_roundtrip()

    def test_legacy_optical_events_cannot_replace_physical_capture(self):
        report=self.adapter_roundtrip(optical=True)
        self.assertEqual(report["optical_status"],"measured")
        self.assertIn("physical motion-to-photon capture/calibration missing",report["suite_issues"])

    def test_missing_integration_is_blocked(self):
        reasons=readiness({},make_plan("smoke")["cases"])
        self.assertTrue(any("integration absent" in r for r in reasons))

    def test_path_traversal_and_hash_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t); (p/"data").write_text("data")
            with self.assertRaises(ValueError): artifact(p,dict(path="../data",sha256="0"))
            with self.assertRaises(ValueError): artifact(p,dict(path="data",sha256="0"))
            self.assertEqual(artifact(p,dict(path="data",sha256=hashlib.sha256(b"data").hexdigest())),p/"data")

    def test_stop_attempted_after_run_error(self):
        actions=[]
        def fake(adapter,action,request,folder,timeout):
            actions.append(action)
            if action=="run": raise ValueError("forced failure")
            return dict(restored=True)
        with tempfile.TemporaryDirectory() as t, patch("runner.call",side_effect=fake), patch("runner.readiness",return_value=[]):
            with self.assertRaisesRegex(ValueError,"forced failure"):
                run("unused",make_plan("smoke"),pathlib.Path(t)/"out")
        self.assertEqual(actions,["probe","run","stop"])


if __name__=="__main__": unittest.main()
