import copy
import importlib.util
import json
from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('pacer_tail', ROOT / 'validation/wivrn-codecs/integration/pacer_tail.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
SPEC = importlib.util.spec_from_file_location('tail_fixture', ROOT / 'prototypes/vr444/experiments/pacer_recent_tail/test_analyze.py')
f = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f)


def settings(**changes):
    return Namespace(**(dict(pacer_window_frames=900, mode='custom-vr444', decoder_backend='cpu-neon',
        pacer_trace=True, source_trace=True, candidate_trace=True) | changes))


def write(root, rows=None, manifest=False):
    root.joinpath('host').mkdir(exist_ok=True)
    root.joinpath(m.TRACE).write_text(''.join(json.dumps(row)+'\n' for row in (rows or f.fixture())))
    provenance = vars(settings()) | dict(source_helper_sha256={name:m.digest((m.OVERLAY/name).read_bytes()) for name in m.HELPERS})
    root.joinpath('provenance.json').write_text(json.dumps(provenance))
    if manifest:
        root.joinpath('hashes.json').write_text(json.dumps({name:m.digest((root/name).read_bytes()) for name in (m.TRACE,'provenance.json')}))


class PacerTail(unittest.TestCase):
    def test_strict_default_and_enabled_control_admission(self):
        self.assertEqual(m.validate(Namespace()), ())
        self.assertEqual(m.configure(Namespace(pacer_window_frames=0), '/nonexistent'), {})
        for value in (True, False, 900.0, '900', None, -1, 359, 1801):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.validate(settings(pacer_window_frames=value))
        for field, value in [('mode','native-hevc'),('decoder_backend','resident'),
                             ('pacer_trace',False),('source_trace',1),('candidate_trace',False)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.validate(settings(**{field:value}))
        for mode in ('custom-vr444','custom-rrb32'):
            for size in (360,900,1800):
                self.assertEqual(m.validate(settings(mode=mode,pacer_window_frames=size)), m.HELPERS)

    def test_exact_source_preflight_and_configuration_has_no_reads_or_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(m.validate(Namespace(), root/'absent'), ())
            with self.assertRaises(ValueError):m.validate(settings(), root)
            for name in m.HELPERS:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((m.OVERLAY/name).read_bytes())
            self.assertEqual(m.validate(settings(),root),m.HELPERS)
            (root/m.HELPERS[-1]).write_text('old or differently built source')
            with self.assertRaisesRegex(ValueError,'differs'):m.validate(settings(),root)
            out=root/'unused'
            with patch.object(m,'bounded',side_effect=AssertionError('configuration read')):
                env=m.configure(settings(),out)
            self.assertFalse(out.exists())
            self.assertEqual(env,dict(WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES='900',WIVRN_CODEC_TEST_PACER_TAIL_TRACE=str(out/m.TRACE)))

    def test_off_rejects_unexpected_and_broken_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(m.evaluate(root,0)['status'],'off')
            self.assertEqual(m.evaluate(root,True)['status'],'fail')
            write(root)
            self.assertEqual(m.evaluate(root,0)['status'],'fail')
            (root/m.TRACE).unlink();(root/m.TRACE).symlink_to(root/'absent')
            self.assertEqual(m.evaluate(root,0)['status'],'fail')
            self.assertEqual(m.evaluate(root,900)['status'],'fail')

    def test_bound_single_trace_read_and_optional_manifest_replay(self):
        for manifest in (False,True):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);write(root,manifest=manifest)
                original=m.bounded;counts=[]
                def bound(path,limit):
                    if Path(path)==root/m.TRACE:counts.append(path)
                    return original(path,limit)
                with patch.object(m,'bounded',side_effect=bound):result=m.evaluate(root,900)
                self.assertEqual(result['status'],'pass',result)
                self.assertEqual(len(counts),1)
                self.assertEqual(result['input_sha256'],m.digest((root/m.TRACE).read_bytes()))
                self.assertEqual(result['existing_run_manifest_verified'],manifest)
                self.assertFalse(result['latency_qualified']);self.assertFalse(result['promotion_eligible'])
                self.assertFalse(result['other_trace_gates_evaluated']);self.assertFalse(result['native_binary_attested'])
                self.assertEqual(result['recorded_native_source_sha256'],json.loads((root/'provenance.json').read_bytes())['source_helper_sha256'])

    def test_provenance_window_source_and_diagnostic_controls_must_match(self):
        for mutation in ('window','bool','source','missing','diagnostic'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);write(root);path=root/'provenance.json';p=json.loads(path.read_bytes())
                if mutation=='window':p['pacer_window_frames']=360
                elif mutation=='bool':p['pacer_window_frames']=True
                elif mutation=='source':p['source_helper_sha256'][m.HELPERS[-1]]='0'*64
                elif mutation=='missing':del p['source_helper_sha256'][m.HELPERS[-1]]
                else:p['candidate_trace']=False
                path.write_text(json.dumps(p))
                self.assertEqual(m.evaluate(root,900)['status'],'fail')

    def test_repaired_hash_does_not_bypass_semantic_or_window_checks(self):
        mutations=[lambda r:r[0].update(window_frames=360),lambda r:r[1].update(valid=False),
                   lambda r:r[2].update(applied_budget_ns=9_000_000),lambda r:r[-1].update(dropped=1),
                   lambda r:r.pop(),lambda r:r[2].update(previous_newest_frame=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);rows=copy.deepcopy(f.fixture());mutation(rows);write(root,rows,manifest=True)
                self.assertEqual(m.evaluate(root,900)['status'],'fail')

    def test_json_and_hash_drift_fail_closed(self):
        for mutation in ('duplicate','nonfinite','stale_hash','provenance_hash','missing','oversized'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);write(root,manifest=True);path=root/m.TRACE
                if mutation=='duplicate':path.write_text(path.read_text().replace('"window_frames": 900','"window_frames": 900, "window_frames": 900'))
                elif mutation=='nonfinite':path.write_text(path.read_text().replace('"margin_ns": 1000000','"margin_ns": NaN'))
                elif mutation=='stale_hash':path.write_bytes(path.read_bytes()+b'\n')
                elif mutation=='provenance_hash':
                    p=root/'provenance.json';p.write_bytes(p.read_bytes()+b' ')
                elif mutation=='missing':path.unlink()
                else:path.write_bytes(b' '*(m.MAX_TRACE+1))
                # Repair hashes on grammar cases to test parsing, not digest gate.
                if mutation in ('duplicate','nonfinite'):
                    h=root/'hashes.json';v=json.loads(h.read_bytes());v[m.TRACE]=m.digest(path.read_bytes());h.write_text(json.dumps(v))
                self.assertEqual(m.evaluate(root,900)['status'],'fail')




class RollingHarness(unittest.TestCase):
    def test_explicit_bank_admission_and_environment(self):
        for value in (None,True,0,'ROLLING','rolling ','unknown'):
            with self.subTest(value=value),self.assertRaises(ValueError):m.validate(settings(pacer_feedback_bank=value))
        with self.assertRaises(ValueError):m.validate(settings(pacer_window_frames=0,pacer_feedback_bank='rolling'))
        self.assertEqual(m.configure(settings(pacer_feedback_bank='rolling'),'/tmp/new')['WIVRN_CODEC_TEST_PACER_BANK_MODE'],'rolling')
        self.assertNotIn('WIVRN_CODEC_TEST_PACER_BANK_MODE',m.configure(settings(),'/tmp/new'))
    def test_actual_sidecar_mode_must_match_recorded_activation(self):
        for requested,recorded,okay in [('rolling','rolling',True),('alternating','rolling',False),('rolling','alternating',False)]:
            with self.subTest(requested=requested,recorded=recorded),tempfile.TemporaryDirectory()as tmp:
                root=Path(tmp);write(root,f.rolling_fixture()if recorded=='rolling'else f.fixture())
                path=root/'provenance.json';v=json.loads(path.read_bytes());v['pacer_feedback_bank']=requested;path.write_text(json.dumps(v))
                r=m.evaluate(root,900,requested);self.assertEqual(r['status'],'pass'if okay else'fail',r)
                if okay:self.assertEqual(r['feedback_bank'],'rolling')
                self.assertEqual(m.evaluate(root,900,'rolling' if requested=='alternating' else 'alternating')['status'],'fail')

if __name__ == '__main__':unittest.main()
