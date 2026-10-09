import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('live',Path(__file__).resolve().parents[1]/'integration/live.py')
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)
class LiveIntegration(unittest.TestCase):
 def test_content_sanity_accepts_flat_shaded_edges_but_not_black_noise(self):
  a=bytes([119,151,151,255])*16+bytes([0,0,255,255])*16
  self.assertTrue(live.scene_content(a)['source_has_scene_content'])
  b=bytes([0,0,0,255])*16+bytes([1,1,1,255])*16
  self.assertFalse(live.scene_content(b)['source_has_scene_content'])
 def test_latency_counts_first_stereo_submission_only(self):
  rows=[dict(left=1,right=1,submit_xr_ns=9_000_000,left_receive_xr_ns=1_000_000,right_receive_xr_ns=2_000_000),
        dict(left=1,right=1,submit_xr_ns=99_000_000,left_receive_xr_ns=1_000_000,right_receive_xr_ns=2_000_000),
        dict(left=2,right=2,submit_xr_ns=19_000_000,left_receive_xr_ns=0,right_receive_xr_ns=20_000_000)]
  result=live.timing_metrics({}, {}, rows)
  self.assertEqual(result['headset_receive_to_submit_ms'],dict(count=2,p50=7,p95=8,p99=8,max=8))
  self.assertEqual(result['unique_stereo_submission_hz'],100)
 def test_empty_evidence_fails(self):
  with tempfile.TemporaryDirectory() as d:
   self.assertEqual(live.evaluate(d,'raw-rgba')['status'],'fail')
 def test_black_frames_do_not_pass_scene_gate(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'host').mkdir();(root/'client/codec-test').mkdir(parents=True)
   for eye in (0,1):
    host=[];decoded=[]
    for frame in range(5):
     host.append(dict(frame=frame,eye=eye,warp=1,payload_bytes=1474560))
     decoded.append(dict(frame=frame,eye=eye,warp=1))
    (root/f'host/encode-{eye}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in host))
    (root/f'client/codec-test/decode-{eye}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in decoded))
    for prefix in ('host','client/codec-test'):
     (root/prefix/f'0-{eye}.rgba').write_bytes(bytes([0,0,0,255])*(576*640))
   (root/'client/codec-test-present.jsonl').write_text(''.join(json.dumps(dict(left=i,right=i,left_warp=1,right_warp=1))+'\n' for i in range(5)))
   result=live.evaluate(root,'raw-rgba')
   self.assertEqual(result['errors'],['missing nondegenerate RGB scene captures for both submitted eyes'])
 def test_decode_warp_mismatch_is_detected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'host').mkdir();(root/'client/codec-test').mkdir(parents=True)
   (root/'host/encode-0.jsonl').write_text(json.dumps(dict(frame=7,eye=0,warp=10,payload_bytes=100))+'\n')
   (root/'client/codec-test/decode-0.jsonl').write_text(json.dumps(dict(frame=7,eye=0,warp=11))+'\n')
   result=live.evaluate(root,'raw-rgba')
   self.assertIn('decode identity mismatch (7, 0)',result['errors'])
 def test_mixed_eye_presentation_is_detected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'client').mkdir()
   (root/'client/codec-test-present.jsonl').write_text(json.dumps(dict(left=7,right=8))+'\n')
   result=live.evaluate(root,'raw-rgba')
   self.assertIn('stereo mismatch',result['errors'])

 def test_pair_utilization_requires_both_eyes_and_identical_warp(self):
  decoded={(1,0):dict(warp=10,pairing='paired',pair_admitted=3),
           (1,1):dict(warp=10,pairing='paired',pair_admitted=3),
           (2,0):dict(warp=20), (2,1):dict(warp=21), (3,0):dict(warp=30)}
  presented=[dict(left=1,right=1),dict(left=1,right=1),dict(left=2,right=2),dict(left=3,right=3)]
  result=live.stereo_metrics(decoded,presented,[dict(pair_admitted=4)])
  self.assertEqual(result['decoded_matching_id_pairs'],2)
  self.assertEqual(result['decoded_coherent_pairs'],1)
  self.assertEqual(result['decoded_eyes_without_coherent_mate'],3)
  self.assertEqual(result['decoded_eye_pair_utilization'],.4)
  self.assertEqual(result['coherent_pairs_submitted'],1)
  self.assertEqual(result['shared_pair_counter_maxima']['pair_admitted'],4)
 def test_empty_stereo_metrics_are_unknown_not_perfect(self):
  result=live.stereo_metrics({},[])
  self.assertIsNone(result['decoded_eye_pair_utilization'])
  self.assertIsNone(result['coherent_pair_submission_fraction'])

 def test_cpu_timing_is_not_reported_as_zero_gpu_work(self):
  rows={(1,0):dict(decoder_backend='cpu-neon',gpu_timing_valid=False,decode_gpu_ms=0,
                    cpu_decode_ms=.7,staging_copy_ms=.2,submit_fence_wait_ms=.4),
        (2,0):dict(decoder_backend='resident',gpu_timing_valid=True,decode_gpu_ms=4,
                    cpu_decode_ms=0,staging_copy_ms=0,submit_fence_wait_ms=5)}
  result=live.timing_metrics({},rows,[])
  self.assertEqual(result['client_decode_gpu_ms']['count'],1)
  self.assertEqual(result['client_decode_gpu_ms']['p50'],4)
  self.assertEqual(result['client_cpu_decode_ms']['count'],1)
  self.assertEqual(result['client_cpu_decode_ms']['p50'],.7)
  self.assertEqual(result['client_staging_copy_ms']['count'],2)
