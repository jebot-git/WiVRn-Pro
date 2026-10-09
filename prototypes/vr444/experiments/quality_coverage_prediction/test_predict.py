import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import predict

class Admission(unittest.TestCase):
    def fixture(self,root):
        records={}
        for i in (1,2,3):
            for eye in (0,1):
                records[f'client/codec-test/quality/capture-{i}-eye-{eye}.json']=dict(capture_id=i,eye=eye,frame=90*i,warp=i,encoded_input_extent=[960,1056],width=1800,height=1980,projection_submitted=True,readback_fence_observed=True)
        records['provenance.json']={'source_helper_sha256':{'server/compositor/foveation.cpp':predict.sha(predict.SOURCE.read_bytes())}}
        return records
    def rejects(self,change,reason):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);records=self.fixture(root);change(records)
            manifest={}
            for name,d in records.items():
                raw=json.dumps(d).encode();p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);manifest[name]=predict.sha(raw)
            raw=json.dumps(manifest).encode();(root/'hashes.json').write_bytes(raw)
            with self.assertRaisesRegex(ValueError,reason):predict.run(root,predict.sha(raw),root/'output')
    def test_boolean_eye(self):self.rejects(lambda r:r['client/codec-test/quality/capture-1-eye-0.json'].update(eye=False),'identity')
    def test_boolean_capture_id(self):self.rejects(lambda r:r['client/codec-test/quality/capture-1-eye-0.json'].update(capture_id=True),'identity')
    def test_stereo_warp(self):self.rejects(lambda r:r['client/codec-test/quality/capture-1-eye-1.json'].update(warp=7),'stereo frame/warp')
    def test_stereo_frame(self):self.rejects(lambda r:r['client/codec-test/quality/capture-1-eye-1.json'].update(frame=91),'stereo frame/warp')
    def test_missing_eye(self):self.rejects(lambda r:r.pop('client/codec-test/quality/capture-3-eye-1.json'),'population')
    def test_wrong_source(self):self.rejects(lambda r:r['provenance.json']['source_helper_sha256'].update({'server/compositor/foveation.cpp':'0'*64}),'source mismatch')
    def test_unsafe_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=json.dumps({'../outside':'0'*64}).encode();(root/'hashes.json').write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'unsafe manifest path'):predict.run(root,predict.sha(raw),root/'output')
    def test_symlink_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);actual=root/'actual';actual.mkdir();(root/'client').symlink_to(actual,target_is_directory=True)
            name='client/codec-test/quality/capture-1-eye-0.json';p=root/name;p.parent.mkdir(parents=True);p.write_text('{}');raw=json.dumps({name:predict.sha(b'{}')}).encode();(root/'hashes.json').write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'symlink'):predict.run(root,predict.sha(raw),root/'output')

if __name__=='__main__':unittest.main()
