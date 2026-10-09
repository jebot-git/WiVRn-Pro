"""Unit fixtures are synthetic; these tests never issue a quality-route pass."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
HERE=Path(__file__).resolve().parents[1]/'integration'
sys.path.insert(0,str(HERE))
import original_reference_job as job
import original_reference_compare as compare
import test_source_mapping as mapping_fixtures
import test_submitted_capture as native_fixtures


class OriginalReference(unittest.TestCase):
    def setUp(self):
        self.f=native_fixtures.CaptureAudit();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.record=mapping_fixtures.mapping(4,4)
        self.record['warp']=21
        bits=mapping_fixtures.bits([-.8,.8,.8,-.8])
        for e in self.record['eyes']:e['source_fov_bits_lrud']=bits
        self.app=mapping_fixtures.app_rows(self.record)
        for eye in (0,1):
            raw=bytes([11+eye,22,33,255])*16
            self.f.put(self.f.run,f'app-source/eye{eye}.source.rgba',raw)
            self.app[1]['views'][eye].update(source_sha256=job.capture.digest(raw),readback_exact=True)
            self.app[1]['views'][eye]['fov']['radians']=[-.8,.8,.8,-.8]
            self.f.put(self.f.run,f'host/90-{eye}.rgba',raw)
        self.f.put(self.f.run,job.mapping.TRACE,b''.join(native_fixtures.encoded(r) for r in mapping_fixtures.trace(self.record)))
        self.f.put(self.f.run,'app-source/app.jsonl',b''.join(native_fixtures.encoded(r) for r in self.app))
        self.f.put(self.f.run,'host/source-trace.jsonl',b''.join(native_fixtures.encoded(r) for r in mapping_fixtures.sources()))
        self.f.rehash();self.run_sha=job.capture.digest((self.f.run/'hashes.json').read_bytes())
        native,_=job.capture.audit(self.f.run,self.f.receipt_path,self.f.receipt_sha,run_hashes_sha256=self.run_sha)
        self.app_receipt=self.f.root/'app-build.json';self.app_receipt.write_bytes(b'{}');self.app_sha=job.capture.digest(b'{}')
        self.proof=dict(native_capture_audit=native,pairs=[dict(frame=90,warp=21)],
            application_audit=dict(source_sha256=[self.app[1]['views'][e]['source_sha256'] for e in (0,1)]),
            run_inputs={name:dict(bytes=(self.f.run/name).stat().st_size,sha256=sha) for name,sha in self.f.getj('hashes.json').items()},
            build_inputs=native['build_inputs'],auditor_sha256={'validation/wivrn-codecs/integration/source_mapping.py':job.capture.digest(Path(job.mapping.__file__).read_bytes())})
    def call(self,cid=1,context=None):
        with mock.patch.object(job.mapping,'audit',return_value=self.proof) as upstream:
            value=job.make_job(self.f.run,self.run_sha,self.f.receipt_path,self.f.receipt_sha,self.app_receipt,self.app_sha,cid,_context=context)
            upstream.assert_called_once_with(self.f.run,self.run_sha,self.f.receipt_path,self.f.receipt_sha,self.app_receipt,self.app_sha,None)
            return value
    def test_adapter_recomputes_mapping_and_keeps_literal_two_eye_bytes(self):
        report,products=self.call();data=products[report['job']['path']]
        self.assertEqual(compare.literal_inputs(data,report['output']),[(self.f.run/f'app-source/eye{e}.source.rgba').read_bytes() for e in (0,1)])
        self.assertTrue(report['source_mapping_recomputed']);self.assertFalse(report['full_goal_qualified'])
        self.assertFalse(report['post_foveation_pixels_used_as_reference']);self.assertFalse(report['actual_native_vertex_bytes_captured'])
        self.assertEqual(report['job']['path'],'capture-1-original-application-input.rlp')
        self.assertEqual([r['application_sequence'] for r in report['independent_inputs']],[19,19])
    def test_mapping_error_cannot_be_replaced_by_passing_json(self):
        with mock.patch.object(job.mapping,'audit',side_effect=ValueError('unbound source mapping')):
            with self.assertRaisesRegex(ValueError,'unbound source'):
                job.make_job(self.f.run,self.run_sha,self.f.receipt_path,self.f.receipt_sha,self.app_receipt,self.app_sha,1)
    def test_capture_identity_and_manifest_drift_reject(self):
        for value in (True,0,9,2):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.call(value)
        (self.f.run/'app-source/eye1.source.rgba').write_bytes(bytes(64))
        with self.assertRaises(ValueError):self.call()
    def test_no_candidate_or_post_foveation_fallback(self):
        path=self.f.run/'app-source/eye0.source.rgba';path.unlink()
        self.f.put(self.f.run,'host/90-0.rgba',bytes([11,22,33,255])*16)
        with self.assertRaises(ValueError):self.call()
    def test_unity_endpoint_admission_and_bad_geometry(self):
        for n in (4,16,1800,1980):self.assertEqual(job.identity_axis(n),[n])
        for n in (True,0,4097):
            with self.assertRaises(ValueError):job.identity_axis(n)
        # A finite valid dimension can also fail exact binary32 endpoints.
        rejected=[]
        for n in range(1,4097):
            try:job.identity_axis(n)
            except ValueError:rejected.append(n)
        self.assertTrue(rejected)
    def test_original_metadata_mutations_fail_before_job(self):
        meta=self.f.getj(job.capture.PREFIX+'capture-1-eye-0.json');raw=(self.f.run/'app-source/eye0.source.rgba').read_bytes()
        changes=[lambda m:m.update(width=5),lambda m:m.update(frame=91),lambda m:m.update(warp=8),
            lambda m:m.update(eye=1),lambda m:m.update(scale_rgba=[.99,1,1,1]),lambda m:m.update(bias_rgba=[1,0,0,0]),
            lambda m:m.update(cutout_mask=[[1,0,0,0],[0]*4,[0]*4]),lambda m:m.update(foveation_x=[1,2,1]),
            lambda m:m['render_pose'].update(position=[1,0,0]),lambda m:m.update(unwarped_fov=[-.81,.8,-.8,.8]),
            lambda m:m.update(alpha_image_bound=True),lambda m:m.update(shader_srgb_decode=True)]
        for change in changes:
            with self.subTest(change=change):
                changed=copy.deepcopy(meta);change(changed)
                with self.assertRaises(ValueError):job.original_eye(changed,self.record,0,raw,self.app[1]['views'][0],raw)
    def prepared(self):
        meta=self.f.getj(job.capture.PREFIX+'capture-1-eye-0.json')
        raw=(self.f.run/'app-source/eye0.source.rgba').read_bytes()
        return job.original_eye(meta,self.record,0,raw,self.app[1]['views'][0],raw)[2]
    def test_distinguishes_one_code_candidate_and_reference_errors(self):
        prepared=self.prepared();raw=prepared['expected_rgba']
        good=job.pixels.compare(prepared,raw,raw);self.assertTrue(good['finite_original_source_to_submitted_exact'])
        y,x=np.argwhere(prepared['mask'])[0];bad=bytearray(raw);bad[(y*4+x)*4]^=1;bad=bytes(bad)
        for reference,candidate in ((bad,raw),(raw,bad),(bad,bad)):
            result=job.pixels.compare(prepared,candidate,reference);self.assertFalse(result['finite_original_source_to_submitted_exact'])
        result=job.pixels.compare(prepared,bad,bad)
        self.assertTrue(result['comparisons']['native_to_replay']['protected_exact'])
        self.assertFalse(result['comparisons']['replay_to_original']['protected_exact'])
    def test_alpha_and_typed_json_are_not_hidden_by_rgb_equality(self):
        prepared=self.prepared();raw=prepared['expected_rgba'];bad=bytearray(raw);bad[3]=254
        result=job.pixels.compare(prepared,raw,bytes(bad))
        self.assertFalse(result['finite_original_source_to_submitted_exact']);self.assertTrue(result['comparisons']['replay_to_original']['protected_exact'])
        with self.assertRaises(ValueError):compare.same_json({'pass':True},{'pass':1},'typed alias')
    def test_compare_calls_fresh_job_and_actual_collector_and_never_full_goal(self):
        report,products=self.call()
        jobdir=self.f.root/'job';jobdir.mkdir()
        wire=products[report['job']['path']];(jobdir/report['job']['path']).write_bytes(wire)
        receipt=jobdir/'audit.json';receipt.write_bytes(native_fixtures.encoded(report));job_sha=job.capture.digest(receipt.read_bytes())
        plan=self.f.root/'plan.json'
        plan.write_bytes(native_fixtures.encoded(dict(replay=dict(source_adapter_sha256=job_sha,job_sha256=job.capture.digest(wire),native_build_receipt_sha256=self.f.receipt_sha,output=report['output']))))
        plan_sha=job.capture.digest(plan.read_bytes())
        def collector(directory,plan_path,runner,out):
            out.mkdir();eyes=[]
            for eye,raw in enumerate(compare.literal_inputs(wire,report['output'])):
                path=out/f'eye-{eye}.raw';path.write_bytes(raw);eyes.append(dict(path=str(path),sha256=job.capture.digest(raw),bytes=len(raw),channel_order='RGBA'))
            return dict(actual_headset_used=True,omission_control_verified=True,native_recorded_identity='recorded-fixture-device',output=report['output'],eyes=eyes,headset_physical_serial='unit-fixture-only',device=dict(device='mock-only'))
        args=(self.f.run,self.run_sha,self.f.receipt_path,self.f.receipt_sha,self.app_receipt,self.app_sha,1,receipt,job_sha,'unused-device',plan,plan_sha,'unused-runner',self.f.root/'comparison')
        actual_make=job.make_job
        def fresh(*args,**kwargs):
            with mock.patch.object(job.mapping,'audit',return_value=self.proof):return actual_make(*args,**kwargs)
        with mock.patch.object(compare.original,'make_job',side_effect=fresh) as recompute, mock.patch.object(compare.replay,'audit',side_effect=collector) as observed:
            value=compare.audit(*args)
            recompute.assert_called_once();observed.assert_called_once()
            self.assertEqual(value['status'],'pass');self.assertFalse(value['full_goal_qualified']);self.assertFalse(value['quality_route_qualified'])
            self.assertFalse(value['clean_visual_qualified'])
        # A newly hashed edited archived PASS is still rejected by recomputation.
        forged=copy.deepcopy(report);forged['frame']=180;receipt.write_bytes(native_fixtures.encoded(forged))
        bad=list(args);bad[8]=job.capture.digest(receipt.read_bytes());bad[-1]=self.f.root/'bad-comparison'
        with mock.patch.object(compare.original,'make_job',return_value=(report,products)), mock.patch.object(compare.replay,'audit') as observed:
            with self.assertRaisesRegex(ValueError,'fresh source-mapping'):compare.audit(*bad)
            observed.assert_not_called()

    def test_literal_parser_rejects_substitution_and_trailing_pixels(self):
        report,products=self.call();wire=products[report['job']['path']]
        for data in (wire+b'\0',wire[:-1],wire[:24]+struct.pack('<I',5)+wire[28:]):
            with self.assertRaises(ValueError):compare.literal_inputs(data,report['output'])

if __name__=='__main__':unittest.main()
