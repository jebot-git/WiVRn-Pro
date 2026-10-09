import importlib.util,pathlib,tempfile,unittest,copy
p=pathlib.Path(__file__).with_name('audit.py');s=importlib.util.spec_from_file_location('recovered_audit',p);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
class Recovery(unittest.TestCase):
 def test_separate_inventory_mutations(self):
  with tempfile.TemporaryDirectory()as d:
   root=pathlib.Path(d);(root/'client').mkdir();p=root/'client/a';p.write_bytes(b'original');sha=a.c.digest(p.read_bytes())
   a.bind_inventory(a.Inputs(),root,{'client/a':sha},exact=True)
   for name,h in [('../escape',sha),('/absolute',sha),('client/a','0'*64)]:
    with self.assertRaises(ValueError):a.bind_inventory(a.Inputs(),root,{name:h},exact=True)
   (root/'client/b').write_bytes(b'not listed')
   with self.assertRaises(ValueError):a.bind_inventory(a.Inputs(),root,{'client/a':sha},exact=True)
 def test_changed_read_and_symlink(self):
  with tempfile.TemporaryDirectory()as d:
   p=pathlib.Path(d)/'a';p.write_bytes(b'a');i=a.Inputs();i.read(p);p.write_bytes(b'b')
   with self.assertRaises(ValueError):i.verify()
   s=pathlib.Path(d)/'s';s.symlink_to(p)
   with self.assertRaises(ValueError):a.Inputs().read(s)
 def test_shared_clock_once_and_peer_splice(self):
  enc=[[],[]];dec=[[],[]]
  for e in(0,1):
   for f in(4,5):
    enc[e].append(dict(frame=f,eye=e,warp=f+5,effective_codec='rrb32',quantizer=1,encode_cpu_wall_ms=2))
    dec[e].append(dict(frame=f,eye=e,warp=f+5,effective_codec='rrb32',rrb_output='cpu',rrb_workers=4,configured_rrb_workers=4,
      rrb_vector_backend='aarch64-neon',pair_decode_scope='stereo_once_reported_per_eye',rrb_generation=2,pair_decode_ms=f,
      staging_copy_ms=1,submit_fence_wait_ms=2,decode_upload_ms=8,queue_wait_ms=0,capture_snapshot_ms=0))
  _,report=a.decoder_costs(enc,dec);self.assertEqual(report['shared_pair_decode_ms']['count'],2)
  for key,value in [('warp',10),('rrb_generation',3),('pair_decode_ms',8),('rrb_workers',2)]:
   bad=copy.deepcopy(dec);bad[1][0][key]=value
   with self.assertRaises(ValueError):a.decoder_costs(enc,bad)
  bad=copy.deepcopy(dec);bad[1].pop()
  with self.assertRaises(ValueError):a.decoder_costs(enc,bad)
 def test_costs_no_nonfinite_or_negative(self):
  for value in(float('nan'),float('inf'),-1,True):
   with self.assertRaises(ValueError):a.stats([value])
  self.assertEqual(a.stats([3,1,2])['p95'],3)
if __name__=='__main__':unittest.main()
