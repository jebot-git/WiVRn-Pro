import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_summary', Path(__file__).with_name('summarize.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class EvidenceTests(unittest.TestCase):
    def test_prewarm_frame_does_not_become_new(self):
        rows = [{'left': 1, 'submit_xr_ns': 10}, {'left': 1, 'submit_xr_ns': 21},
                {'left': 2, 'submit_xr_ns': 22}, {'left': 2, 'submit_xr_ns': 23}]
        self.assertEqual(m.warm_first(rows, 20), [rows[2]])

    def test_nearest_rank_not_interpolated(self):
        self.assertEqual(m.dist(list(range(20)))['p95'], 18)

    def test_focus_uses_pid_and_xr_window(self):
        def line(pid, t, a, b):
            return f'10-09 15:00:00.000 {pid} 100 I WiVRn : Session state changed at timestamp {t}: XR_SESSION_STATE_{a} => XR_SESSION_STATE_{b}'
        initial = line(7, 10, 'VISIBLE', 'FOCUSED')
        other = line(8, 25, 'FOCUSED', 'VISIBLE')
        after = line(7, 50, 'FOCUSED', 'VISIBLE')
        self.assertTrue(m.focus('\n'.join([initial, other, after]), '7', 20, 40)['continuously_focused_observed'])
        loss = line(7, 25, 'FOCUSED', 'VISIBLE')
        recovered = line(7, 30, 'VISIBLE', 'FOCUSED')
        result = m.focus('\n'.join([initial, loss, recovered]), '7', 20, 40)
        self.assertFalse(result['continuously_focused_observed'])
        self.assertEqual(len(result['warm_transitions']), 2)
        with self.assertRaisesRegex(ValueError, 'unknown focus'):
            m.focus(loss, '7', 20, 40)

    def test_bound_and_concurrent_input_mutation_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p/'record').write_bytes(b'original')
            inputs = m.Inputs({'root': p})
            inputs.read('root', 'record', m.digest(b'original'), 'bytes')
            (p/'record').write_bytes(b'mutated')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                inputs.read('root', 'record', m.digest(b'original'), 'bytes')
            with self.assertRaisesRegex(ValueError, 'input changed'):
                inputs.unchanged()


if __name__ == '__main__':
    unittest.main()
