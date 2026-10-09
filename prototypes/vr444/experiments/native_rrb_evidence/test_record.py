import hashlib,json
from pathlib import Path
import tempfile
import unittest
import record

class Record(unittest.TestCase):
    def test_bound_manifest_bytes_symlinks_and_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'data').write_bytes(b'original');raw=json.dumps({'data':record.sha(b'original')}).encode();(p/'hashes.json').write_bytes(raw)
            x=record.Inputs(p,record.sha(raw));x.verify();self.assertEqual(x.read('data'),b'original')
            with self.assertRaises(ValueError):record.Inputs(p,'0'*64)
            (p/'data').write_bytes(b'changed')
            with self.assertRaises(ValueError):x.verify()
            (p/'data').unlink();(p/'other').write_bytes(b'original');(p/'data').symlink_to(p/'other')
            with self.assertRaises(ValueError):x.verify()
    def test_mapping_does_not_infer_original_source_equality(self):
        def meta(x,y,w,h):return dict(width=w,height=h,encoded_input_extent=[sum(x),sum(y)],foveation_x=x,foveation_y=y,capture_id=1,frame=90,warp=7,eye=0,encoded_input_fov_left_right_down_up_radians=[-.5,.5,-.5,.5])
        exact=record.mapping(meta([8],[8],8,8));self.assertTrue(exact['central10_all_cells_ratio1']);self.assertEqual(exact['central10_cells_with_source_span_above1'],0)
        reduced=record.mapping(meta([2,1,2],[2,1,2],9,9));self.assertFalse(reduced['central10_all_cells_ratio1']);self.assertGreater(reduced['central10_cells_with_source_span_above1'],0)
        with self.assertRaises(ValueError):record.mapping(meta([2,1,2],[2,1,2],8,9))
    def test_exact_rank_and_no_nonfinite(self):
        self.assertEqual(record.quantiles(list(range(1,21)))['p95'],19)
        for bad in ([],[float('nan')],[float('inf')],[-1]):
            with self.assertRaises(ValueError):record.quantiles(bad)
    def test_application_space_cannot_be_relabelled(self):
        for space in ('VIEW','LOCAL'):
            plan=dict(schema=2,source_reference_space=space)
            execution=dict(source_application_space=space,latency_eligible=False)
            prov=dict(scene_reference_space=space.title());app=dict(source_reference_space=space)
            self.assertEqual(record.quality_space(plan,execution,prov,app),space)
            for bad in ('VIEW','LOCAL','Stage',None):
                if bad==space:continue
                with self.assertRaises(ValueError):record.quality_space(plan,dict(execution,source_application_space=bad),prov,app)
                with self.assertRaises(ValueError):record.quality_space(plan,execution,prov,dict(app,source_reference_space=bad))
        self.assertEqual(record.quality_space(dict(schema=1),dict(source_application_space='VIEW',latency_eligible=False),dict(scene_reference_space='View'),dict(source_reference_space='VIEW')),'VIEW')
        with self.assertRaises(ValueError):record.quality_space(dict(schema=1,source_reference_space='LOCAL'),dict(source_application_space='LOCAL',latency_eligible=False),dict(scene_reference_space='Local'),dict(source_reference_space='LOCAL'))

    def test_strict_json_identity(self):
        for data in ('{"frame":1,"frame":2}','{"duration":NaN}'):
            with self.assertRaises(ValueError):record.json_bytes(data)

if __name__=='__main__':unittest.main()
