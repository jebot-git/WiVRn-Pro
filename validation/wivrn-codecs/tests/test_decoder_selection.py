import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('live_selection',Path(__file__).resolve().parents[1]/'integration/live.py')
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)
class DecoderSelection(unittest.TestCase):
 def test_actual_each_eye_required(self):
  rows={(1,0):dict(decoder_kernel='coop128dc',decoder_backend='resident',compression_policy='lz4'),(1,1):dict(decoder_kernel='coop128dc',decoder_backend='resident',compression_policy='lz4')}
  self.assertEqual(live.decoder_selection_metrics(rows,'coop128dc','resident','lz4')['status'],'verified')
  rows[(1,1)]['decoder_kernel']='coop128'
  self.assertEqual(live.decoder_selection_metrics(rows,'coop128dc','resident','lz4')['status'],'mismatch')
 def test_missing_evidence_unverified(self):
  for rows in ({},{(1,0):dict(decoder_kernel='coop128dc')}):
   self.assertEqual(live.decoder_selection_metrics(rows,'coop128dc','resident','lz4')['status'],'unverified')
 def test_policy_and_backend_are_checked(self):
  row=dict(decoder_kernel='coop128dc',decoder_backend='readback',compression_policy='none')
  self.assertEqual(live.decoder_selection_metrics({(1,0):row},'coop128dc','resident','lz4')['status'],'mismatch')
