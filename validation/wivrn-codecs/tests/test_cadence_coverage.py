import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('cadence_coverage_test',ROOT/'prototypes/vr444/experiments/selection_policy/cadence_coverage.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
spec=importlib.util.spec_from_file_location('cadence_coverage_policy',ROOT/'validation/wivrn-codecs/integration/latency_policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)


def held_endpoint_fixture():
    rows=[];frames={};frame=0
    for index in range(2431):
        submit=1_000_000_000+index*1_000_000_000//90
        if index<180 or 1080<=index<=1980:
            frame+=1
            frames[frame]=dict(left=frame,right=frame,left_warp=frame,right_warp=frame,
                left_receive_xr_ns=submit-1_000_000,right_receive_xr_ns=submit-1_000_000,
                left_decode_ready_xr_ns=submit-500_000,right_decode_ready_xr_ns=submit-500_000)
        rows.append(dict(frames[frame],submit_xr_ns=submit))
    return rows


class CadenceCoverageTests(unittest.TestCase):
    def test_synthetic_endpoint_holds_do_not_change_existing_first_use_metric(self):
        rows=held_endpoint_fixture();original=copy.deepcopy(rows)
        report=c.analyze(rows);gate=policy.evaluate(rows,policy.load_policy()['profile'])
        self.assertEqual(gate['status'],'pass',gate['errors'])
        self.assertEqual(report['warm_first_encoded_frames'],901)
        self.assertEqual(report['original_first_to_last_new_frame_hz'],90)
        self.assertEqual(report['conservative_full_observation_transition_hz'],36)
        self.assertEqual(report['observed_prefix_without_new_frame_ns'],10_000_000_000)
        self.assertEqual(report['observed_suffix_without_new_frame_ns'],5_000_000_000)
        self.assertEqual(report['endpoint_coverage_fraction'],.4)
        self.assertGreater(report['all_warm_presented_stereo_age_ms']['p95'],1000)
        self.assertEqual(rows,original)
        self.assertFalse(report['latency_gate_changed']);self.assertFalse(report['promotion_eligible'])

    def test_all_warm_frames_new_has_identical_endpoint_and_full_span_rate(self):
        rows=[]
        for i in range(1000):
            submit=1_000_000_000+i*1_000_000_000//90
            rows.append(dict(left=i,right=i,left_warp=i,right_warp=i,submit_xr_ns=submit,
                left_receive_xr_ns=submit-1_000_000,right_receive_xr_ns=submit-1_000_000,
                left_decode_ready_xr_ns=submit-500_000,right_decode_ready_xr_ns=submit-500_000))
        r=c.analyze(rows)
        self.assertEqual(r['endpoint_coverage_fraction'],1)
        self.assertAlmostEqual(r['original_first_to_last_new_frame_hz'],r['conservative_full_observation_transition_hz'])
        self.assertEqual(r['observed_prefix_without_new_frame_ns'],0);self.assertEqual(r['observed_suffix_without_new_frame_ns'],0)

    def test_prewarm_identity_does_not_become_new_after_cutoff(self):
        rows=held_endpoint_fixture()[:1080];r=c.analyze(rows)
        self.assertEqual(r['warm_first_encoded_frames'],0)
        self.assertEqual(r['conservative_full_observation_transition_hz'],0)
        self.assertIsNone(r['original_first_to_last_new_frame_hz'])
        self.assertEqual(r['endpoint_coverage_fraction'],0)
        self.assertEqual(r['observed_prefix_without_new_frame_ns'],r['full_observed_warm_span_ns'])
        self.assertEqual(r['observed_suffix_without_new_frame_ns'],r['full_observed_warm_span_ns'])

    def test_absent_submissions_are_separate_from_observed_endpoint_holds(self):
        rows=held_endpoint_fixture();rows=rows[:180]+rows[1080:]
        r=c.analyze(rows)
        self.assertEqual(r['cutoff_to_first_warm_presentation_ns'],10_000_000_000)
        self.assertEqual(r['observed_prefix_without_new_frame_ns'],0)
        self.assertEqual(r['full_observed_warm_span_ns'],15_000_000_000)
        self.assertEqual(r['conservative_full_observation_transition_hz'],60)

    def test_clock_translation_preserves_all_intervals_and_age(self):
        rows=held_endpoint_fixture();before=c.analyze(rows)
        for row in rows:
            for key in row:
                if key.endswith('_xr_ns'):row[key]+=10**12
        after=c.analyze(rows)
        for key in ('full_observed_warm_span_ns','cutoff_to_first_warm_presentation_ns','new_frame_endpoint_span_ns','observed_prefix_without_new_frame_ns',
                    'observed_suffix_without_new_frame_ns','endpoint_coverage_fraction','original_first_to_last_new_frame_hz',
                    'conservative_full_observation_transition_hz','maximum_between_new_frames_ns','maximum_observed_submission_gap_ns','all_warm_presented_stereo_age_ms'):
            self.assertEqual(before[key],after[key])

    def test_invalid_stereo_regression_and_reused_metadata_fail_closed(self):
        for change in ('eye','clock','frame','receipt'):
            rows=held_endpoint_fixture()
            if change=='eye':rows[-1]['right']+=1
            elif change=='clock':rows[-1]['submit_xr_ns']=rows[-2]['submit_xr_ns']
            elif change=='frame':rows[-1]['left']=rows[-1]['right']=1
            else:rows[-1]['left_receive_xr_ns']-=1
            with self.subTest(change=change),self.assertRaises(ValueError):c.analyze(rows)
        with self.assertRaises(ValueError):c.analyze(held_endpoint_fixture()[:100])

    def test_rewritten_consistent_manifest_is_not_original_acquisition(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in c.RECORDED_MANIFESTS:
                run=root/name;run.mkdir();(run/'hashes.json').write_text(json.dumps({}))
            with self.assertRaisesRegex(ValueError,'manifest identity differs'):c.cohort(root)
