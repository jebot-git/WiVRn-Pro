"""Larger quality captures require explicit profile, bounded wire and exact identity."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
import zlib

HERE = Path(__file__).resolve().parents[1]/'integration'
def module(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
live=module('live');compare=module('native_compare')

class QualityGeometry(unittest.TestCase):
    def args(self, **changes):
        values=dict(quality_stream_scale=.5,quality_capture=True,source_trace=True,
                    quality_application_plan=Path('/explicit/plan.json'),mode='custom-rrb32',
                    rrb_source='q1',q=1,compression='none')
        return SimpleNamespace(**(values|changes))

    def test_only_explicit_quality_profile_can_change_scale(self):
        for scale in (.4,.5,.6):self.assertEqual(compare.quality_stream_scale(self.args(quality_stream_scale=scale)),scale)
        self.assertEqual(compare.quality_stream_scale(SimpleNamespace()),.3)
        for change in [dict(quality_stream_scale=v) for v in (True,float('nan'),.3,.7,'0.5')]+[
            dict(quality_capture=False),dict(source_trace=False),dict(quality_application_plan=None),
            dict(mode='custom-vr444'),dict(rrb_source='vr444'),dict(q=8),dict(compression='lz4')]:
            with self.subTest(change=change),self.assertRaises(ValueError):compare.quality_stream_scale(self.args(**change))

    def fixture(self, root):
        provenance=dict(quality_geometry_profile=True,quality_application=True,quality_capture=True,
                        source_trace=True,rrb_source='q1',custom_q=1,custom_compression='none',
                        stream_scale=.5,geometry=None)
        (root/'provenance.json').write_text(json.dumps(provenance))
        path=root/'90-0.rgba';body=b'bounded independent envelope body'
        header=[0x31544357,4,960,1056,0,len(body),90,123,zlib.crc32(body),0]
        wire=struct.pack('<6I2Q2I',*header)+body;path.with_suffix('.envelope').write_bytes(wire)
        rows={(90,0):dict(warp=123,payload_bytes=len(wire),effective_codec='rrb32',source_contract='post_foveation_rgb_exact')}
        return path,provenance,header,body,rows

    def test_fixed_profile_does_not_consume_dynamic_envelope(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(live.capture_geometry(root,'rrb32-q1',root/'90-0.rgba',{}),(576,640))

    def test_explicit_profile_uses_bound_envelope_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path,_,_,_,rows=self.fixture(root)
            self.assertEqual(live.capture_geometry(root,'rrb32-q1',path,rows),(960,1056))
            with self.assertRaises(ValueError):live.capture_geometry(root,'rrb32-vr444',path,rows)

    def test_profile_splices_rejected(self):
        for key,value in [('quality_application',False),('source_trace',False),('quality_capture',False),
                          ('custom_q',8),('custom_compression','lz4'),('stream_scale',True),('geometry',[960,1056])]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path,p,_,_,rows=self.fixture(root);p[key]=value
                (root/'provenance.json').write_text(json.dumps(p))
                with self.assertRaises(ValueError):live.capture_geometry(root,'rrb32-q1',path,rows)

    def test_wire_identity_crc_shape_and_size_rejected(self):
        for index,value in [(0,0),(1,2),(2,4097),(2,1),(2,576),(3,640),(3,4097),(4,1),(5,1),(6,91),(7,124),(8,0),(9,1)]:
            with self.subTest(index=index,value=value),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path,_,header,body,rows=self.fixture(root);header[index]=value
                path.with_suffix('.envelope').write_bytes(struct.pack('<6I2Q2I',*header)+body)
                with self.assertRaises(ValueError):live.capture_geometry(root,'rrb32-q1',path,rows)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path,_,_,_,rows=self.fixture(root)
            path.with_suffix('.envelope').write_bytes(bytes(262145))
            with self.assertRaises(ValueError):live.capture_geometry(root,'rrb32-q1',path,rows)

    def test_wrong_encode_event_rejected(self):
        for key,value in [('warp',124),('payload_bytes',0),('effective_codec','vr444'),('source_contract','decoded_vr444_rgb_exact')]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path,_,_,_,rows=self.fixture(root);rows[(90,0)][key]=value
                with self.assertRaises(ValueError):live.capture_geometry(root,'rrb32-q1',path,rows)

if __name__=='__main__':unittest.main()
