import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import audit
import plan

BINARY = Path(os.environ['QUALITY_STIMULI_BINARY']) if 'QUALITY_STIMULI_BINARY' in os.environ else None

def floats(values):
    return [struct.unpack('<I', struct.pack('<f', x))[0] for x in values]

def fixture(path):
    h = dict(type='header', schema=1, pattern='rgb-ramp', source_generation='direct_encoded_srgb_byte_upload',
        source_stage='application_swapchain_before_compositor', quality_only=True, latency_comparable=False,
        width_requested=17, height_requested=33, frame_limit=2, seconds_limit=1, space='VIEW',
        format=43, format_name='R8G8B8A8_SRGB', opaque=True, top_left_origin=True)
    rows = [h]; views = []
    for eye in (0, 1):
        data = audit.expected('rgb-ramp', 17, 33, eye); name = f'eye{eye}.source.rgba'; (path/name).write_bytes(data)
        rows.append(dict(type='eye', eye=eye, width=17, height=33, recommended_width=17, recommended_height=33,
            source_file=name, source_bytes=len(data), source_sha256=audit.sha(data), swapchain_images=2))
        name = f'eye{eye}-image0.readback.rgba'; (path/name).write_bytes(data)
        rows.append(dict(type='image_capture', eye=eye, image_index=0, file=name, sha256=audit.sha(data), bytes=len(data), exact_source=True))
        views.append(dict(eye=eye, image_index=0, width=17, height=33, source_sha256=audit.sha(data), readback_exact=True,
            pose=dict(orientation=[0.,0.,0.,1.], orientation_bits=floats([0,0,0,1]), position=[0.,0.,0.], position_bits=floats([0,0,0])),
            fov=dict(radians=[-.5,.5,.5,-.5], bits=floats([-.5,.5,.5,-.5]))))
    rows.append(dict(type='frame_gate', sequence=1, should_render=True, locate_called=True,
                     view_state_flags=3, required_valid_flags=3, decision='admitted', session_state=5))
    rows.append(dict(type='frame', sequence=1, predicted_display_time=10000000, predicted_display_period=11111111,
                     should_render=True, locate_called=True, view_state_flags=3, decision='admitted', projection_submitted=True, views=views))
    rows.append(dict(type='footer', complete=True, frames=1, submitted=1, stop_reason='frame_limit', session_ended=True, quality_only=True, latency_comparable=False))
    return rows

def save(path, rows): (path/'app.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))

class Audit(unittest.TestCase):
    def test_independent_cpp_fixture_oracle(self):
        self.assertIsNotNone(BINARY, 'set QUALITY_STIMULI_BINARY to the compiled application')
        for name in audit.PATTERNS:
            for width, height in ((1,1),(1,17),(17,1),(17,33),(64,32),(576,640)):
                with tempfile.TemporaryDirectory() as temp:
                    output = Path(temp)/'fixture'
                    result = subprocess.run([str(BINARY), '--emit-fixtures', '--pattern', name, '--width', str(width),
                        '--height', str(height), '--output', str(output)], capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(audit.fixtures(output)['status'], 'pass')
                    self.assertNotEqual((output/'eye0.source.rgba').read_bytes(), (output/'eye1.source.rgba').read_bytes())

    def test_invalid_cli_never_starts_xr(self):
        self.assertIsNotNone(BINARY)
        with tempfile.TemporaryDirectory() as temp:
            base = [str(BINARY), '--emit-fixtures', '--pattern', 'chroma-grid', '--width', '17', '--height', '33', '--output', str(Path(temp)/'out')]
            for extra in (['--frames','0'],['--frames','10801'],['--seconds','121'],['--unknown','x'],['--width','17']):
                r = subprocess.run(base+extra, capture_output=True, timeout=10); self.assertNotEqual(r.returncode,0);self.assertFalse((Path(temp)/'out').exists())
            for w,h in (('0','1'),('4097','1'),('4096','1025'),('-1','1'),('true','1')):
                args=base.copy();args[5]=w;args[7]=h
                r=subprocess.run(args,capture_output=True,timeout=10);self.assertNotEqual(r.returncode,0)

    def test_actual_capture_stereo_and_bits(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);rows=fixture(path);save(path,rows);r=audit.application(path)
            self.assertEqual(r['submitted_stereo_frames'],1);self.assertFalse(r['post_foveation_comparison_performed'])
            self.assertEqual(r['captured_swapchain_images'],2)

    def test_explicit_space_and_legacy_cannot_be_relabeled(self):
        for space in ('VIEW','LOCAL'):
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp);rows=fixture(path)
                rows[0].update(schema=2,space=space,source_reference_space=space,locate_and_layer_use_same_space=True)
                rows[-2]['source_reference_space']=space;save(path,rows)
                self.assertEqual(audit.application(path)['source_reference_space'],space)
                rows[-2]['source_reference_space']='LOCAL' if space=='VIEW' else 'VIEW';save(path,rows)
                with self.assertRaises(ValueError):audit.application(path)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);rows=fixture(path);rows[0]['space']='LOCAL';save(path,rows)
            with self.assertRaises(ValueError):audit.application(path)

    def test_explicit_clock_conversion_and_legacy_not_relabelled(self):
        changes=[lambda r:r[0].update(converted_clock='XR_TIME'),lambda r:r[0].update(time_conversion_extension='inferred'),
            lambda r:r[-2].update(predicted_display_time_monotonic_ns=True),
            lambda r:r[-2]['predicted_display_time_timespec'].update(tv_nsec=10**9),
            lambda r:r[-2]['predicted_display_time_timespec'].update(tv_sec=-1),
            lambda r:r[-2].update(predicted_display_time_monotonic_ns=1000000003),
            lambda r:r[0].update(schema=2)]
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);rows=fixture(path);rows[0].update(schema=3,space='LOCAL',source_reference_space='LOCAL',locate_and_layer_use_same_space=True,
                time_conversion_extension='XR_KHR_convert_timespec_time',time_conversion_function='xrConvertTimeToTimespecTimeKHR',converted_clock='CLOCK_MONOTONIC')
            rows[-2].update(source_reference_space='LOCAL',predicted_display_time_timespec=dict(tv_sec=1,tv_nsec=2),predicted_display_time_monotonic_ns=1000000002)
            save(path,rows);self.assertEqual(audit.application(path)['time_conversion'],'XR_KHR_convert_timespec_time/CLOCK_MONOTONIC')
            for mutate in changes:
                bad=copy.deepcopy(rows);mutate(bad);save(path,bad)
                with self.assertRaises(ValueError):audit.application(path)

    def test_metadata_mutations(self):
        changes = [lambda r:r[0].update(source_generation='client_decode'), lambda r:r[0].update(latency_comparable=True),
            lambda r:r[0].update(format=37), lambda r:r[-1].update(submitted=2), lambda r:r[-1].update(complete=1),
            lambda r:r[-2]['views'][1].update(eye=0), lambda r:r[-2]['views'][1].update(image_index=1),
            lambda r:r[-2]['views'][1].update(readback_exact=1), lambda r:r[-2]['views'][1]['pose']['orientation_bits'].__setitem__(0,1),
            lambda r:r[-2]['views'][1]['fov']['radians'].__setitem__(0,float('nan')),
            lambda r:r[-2].update(projection_submitted=False), lambda r:r[-2].update(sequence=2),
            lambda r:r.pop(4), lambda r:r[-1].update(frames=0), lambda r:r[0].update(schema=True),
            lambda r:r[-1].update(submitted=True), lambda r:r[-2].update(view_state_flags=1),
            lambda r:r[-2].update(locate_called=False), lambda r:r[-3].update(required_valid_flags=1),
            lambda r:r.pop(-3)]
        for change in changes:
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp);rows=fixture(path);change(rows);save(path,rows)
                with self.assertRaises((ValueError,KeyError,TypeError)):audit.application(path)

    def test_corrupt_source_and_readback(self):
        for name in ('eye0.source.rgba','eye1-image0.readback.rgba'):
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp);rows=fixture(path);save(path,rows);data=bytearray((path/name).read_bytes());data[8]^=1;(path/name).write_bytes(data)
                with self.assertRaises(ValueError):audit.application(path)

    def test_symlink_and_incomplete_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);rows=fixture(path);save(path,rows)
            src=path/'eye1-image0.readback.rgba';src.unlink();src.symlink_to(path/'eye1.source.rgba')
            with self.assertRaises(ValueError):audit.application(path)

    def test_rehashed_source_and_readback_still_require_original_oracle(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);rows=fixture(path)
            data=bytearray((path/'eye0.source.rgba').read_bytes());data[9]^=1;data=bytes(data)
            (path/'eye0.source.rgba').write_bytes(data);(path/'eye0-image0.readback.rgba').write_bytes(data)
            rows[1]['source_sha256']=audit.sha(data);rows[2]['sha256']=audit.sha(data);rows[-2]['views'][0]['source_sha256']=audit.sha(data)
            save(path,rows)
            with self.assertRaises(ValueError):audit.application(path)
            save(path,rows[:-1])
            with self.assertRaises(ValueError):audit.application(path)

class Plan(unittest.TestCase):
    def test_snapshot_and_bounded_quality_argv(self):
        self.assertIsNotNone(BINARY)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); binary=root/'original';binary.write_bytes(BINARY.read_bytes());digest=audit.sha(binary.read_bytes())
            result=plan.prepare(binary,digest,root/'bundle',root/'capture','rgb-ramp',576,640,900,30)
            binary.write_bytes(b'changed after planning')
            self.assertEqual(audit.sha(Path(result['executable']).read_bytes()),digest)
            self.assertEqual(result['application'][:4],['/usr/bin/timeout','-k','2','40'])
            self.assertFalse(result['latency_comparable']);self.assertFalse((root/'capture').exists())
            self.assertEqual(result['required_native_contract']['q'],1)

    def test_plan_rejects_bad_pin_and_overlapping_outputs(self):
        self.assertIsNotNone(BINARY)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);digest=audit.sha(BINARY.read_bytes())
            with self.assertRaises(ValueError):plan.prepare(BINARY,'0'*64,root/'b',root/'c','rgb-ramp',17,33)
            with self.assertRaises(ValueError):plan.prepare(BINARY,digest,root/'b',root/'b'/'capture','rgb-ramp',17,33)
            with self.assertRaises(ValueError):plan.prepare(BINARY,digest,root/'b',root/'c','rgb-ramp',4096,1025)

if __name__=='__main__':unittest.main()
