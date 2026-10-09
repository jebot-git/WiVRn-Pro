import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

SPEC = importlib.util.spec_from_file_location('research_quality', Path(__file__).parents[1]/'integration/research_quality.py')
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


class Quality(unittest.TestCase):
    def setUp(self):
        self.w, self.h = 64, 48
        self.x, self.y = quality.perspective_edges(self.w, self.h, [-.8, .9, -.7, .8])
        self.mask = quality.protected_mask(self.w, self.h, self.x, self.y)
        y, x = np.indices((self.h, self.w))
        self.image = np.stack(((x % 2)*255, (y % 2)*255, (x*13+y*17)%256, np.full_like(x, 255)), -1).astype(np.uint8)

    def test_exact_all_channels(self):
        r = quality.compare_rgba(self.image.tobytes(), self.image.tobytes(), self.w, self.h, self.mask)
        self.assertTrue(r['protected_exact'])
        self.assertTrue(r['full_rgb_exact'])
        self.assertGreater(r['protected_pixels'], 0)

    def test_one_code_chroma_error_fails(self):
        image = self.image.copy()
        y, x = np.argwhere(self.mask)[0]
        image[y, x, 2] ^= 1
        r = quality.compare_rgba(self.image.tobytes(), image.tobytes(), self.w, self.h, self.mask)
        self.assertFalse(r['protected_exact'])
        self.assertEqual(r['protected_max_absolute_code_error'], 1)

    def test_periphery_separate_from_protected(self):
        image = self.image.copy()
        y, x = np.argwhere(~self.mask)[0]
        image[y, x, 0] ^= 255
        r = quality.compare_rgba(self.image.tobytes(), image.tobytes(), self.w, self.h, self.mask)
        self.assertTrue(r['protected_exact'])
        self.assertFalse(r['full_rgb_exact'])

    def test_subsampling_damage_fails(self):
        image = self.image.copy()
        image[:, 1::2, :3] = image[:, ::2, :3]
        r = quality.compare_rgba(self.image.tobytes(), image.tobytes(), self.w, self.h, self.mask)
        self.assertFalse(r['protected_exact'])

    def test_footprint_not_pixel_center(self):
        # One very wide footprint intersects the cone even if its center is out.
        radius = math.tan(math.radians(10))
        m = quality.protected_mask(2, 2, [-1, radius/2, 1], [1, -radius/2, -1])
        self.assertTrue(m.all())

    def test_invalid_mappings(self):
        for x, y in ((self.x[::-1], self.y), (self.x, self.y[::-1]),
                     (np.full_like(self.x, np.nan), self.y), (self.x[:3], self.y),
                     (np.linspace(-.1, .1, self.w+1), self.y)):
            with self.assertRaises(ValueError):
                quality.protected_mask(self.w, self.h, x, y)
        with self.assertRaises(ValueError):
            quality.shape(4096, 1025)

    def test_bound_actual_bytes_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def put(name, data):
                (root/name).write_bytes(data)
                return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}
            descriptor = put('a.rgba', self.image.tobytes())
            projection = put('projection.json', json.dumps({'kind': 'unwarped-perspective',
                'geometry': [self.w, self.h], 'frame': 12, 'warp': 23, 'eye': 0,
                'fov_radians': [-.8, .9, -.7, .8]}).encode())
            capture = dict(stage='decoded-source', channel_format='RGBA8',
                reference_boundary='post-host-foveation-rgb', frame=12, warp=23, eye=0,
                geometry=[self.w, self.h], projection=projection, reference=descriptor, candidate=descriptor)
            r = quality.compare_capture(quality.BoundInputs(root), capture)
            self.assertEqual(r['status'], 'pass')
            self.assertFalse(r['full_goal_qualified'])
            self.assertFalse(r['submitted_pixel_equality_verified'])
            capture['eye'] = 1
            with self.assertRaises(ValueError):
                quality.compare_capture(quality.BoundInputs(root), capture)
            (root/'a.rgba').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                quality.BoundInputs(root).read(descriptor)

    def test_path_and_nonfinite_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = quality.BoundInputs(tmp)
            for name in ('../escape', '/absolute'):
                with self.assertRaises(ValueError):
                    inputs.read({'path': name, 'sha256': '0'*64})
            raw = b'{"x": NaN}'
            (Path(tmp)/'bad.json').write_bytes(raw)
            with self.assertRaises(ValueError):
                inputs.json({'path': 'bad.json', 'sha256': hashlib.sha256(raw).hexdigest()})

    def test_either_goal_is_enough_for_research(self):
        achieved = {'status': 'pass', 'goal_verified': True, 'evidence_integrity_verified': True}
        failed = {'status': 'fail', 'goal_verified': False, 'evidence_integrity_verified': True}
        for a, b, expected in ((achieved, failed, ['latency']), (failed, achieved, ['quality']),
                                (achieved, achieved, ['latency', 'quality']), (failed, failed, [])):
            r = quality.research_decision(a, b)
            self.assertEqual(r['qualified_routes'], expected)
            self.assertEqual(r['qualified_for_further_research'], bool(expected))
            self.assertFalse(r['promotion_eligible'])
        self.assertFalse(quality.research_decision({'status': 'pass'}, None)['qualified_for_further_research'])
        self.assertFalse(quality.research_decision(achieved | {'evidence_integrity_verified': False}, None)['qualified_for_further_research'])


if __name__ == '__main__':
    unittest.main()
