# SPDX-License-Identifier: MIT
import copy
import math
import unittest

import alignment as c


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.transform = {'position': [.7, -.03, -1.2],
                          'rotation': [0, math.sin(.8), 0, math.cos(.8)]}
        self.qposes = {'left_hand': {'position': [-.4, .8, -.6], 'rotation': c.unit([.4, .2, .1, .7])},
                       'right_hand': {'position': [.8, .7, -.9], 'rotation': c.unit([-.2, .3, .6, .1])}}
        self.pposes = {h: c.apply(self.transform, p) for h, p in self.qposes.items()}
        self.quest = self.samples(self.qposes, 'quest')
        self.before = self.samples(self.pposes, 'pc')
        self.after = self.samples(self.pposes, 'pc', offset=10_000_000_000)

    def samples(self, poses, source, offset=0):
        rows = []
        for i in range(41):
            devices = copy.deepcopy(poses)
            for p in devices.values():
                p.update(valid=True, flags=15, pose_space='grip', interaction_profile='touch')
                # Quaternion sign flips represent the exact same orientation.
                if i % 2:
                    p['rotation'] = [-v for v in p['rotation']]
            rows.append({'identity': ['broker', source, 1], 'sample_time_ns': str(offset+i*100_000_000), 'devices': devices})
        return rows

    def test_recovers_known_transform_and_held_out_controller(self):
        report = c.solve(self.before, self.quest, self.after)
        self.assertLess(c.residual(report['transform'], self.transform)['position_m'], 1e-10)
        self.assertLess(c.residual(report['transform'], self.transform)['angle_deg'], 1e-5)
        self.assertLess(report['validation']['right_hand']['position_m'], 1e-10)

    def test_rejects_right_controller_moved_between_apps(self):
        for row in self.quest:
            row['devices']['right_hand']['position'][0] += .1
        with self.assertRaisesRegex(ValueError, 'Independent controller'):
            c.solve(self.before, self.quest, self.after)

    def test_rejects_round_trip_motion(self):
        for row in self.after:
            row['devices']['left_hand']['position'][1] += .05
        with self.assertRaisesRegex(ValueError, 'moved or tracking shifted'):
            c.solve(self.before, self.quest, self.after)

    def test_rejects_jitter(self):
        self.quest[10]['devices']['left_hand']['position'][0] += .04
        with self.assertRaisesRegex(ValueError, 'not stable'):
            c.solve(self.before, self.quest, self.after)

    def test_rejects_inferred_pose(self):
        self.quest[0]['devices']['left_hand']['flags'] = 3
        with self.assertRaisesRegex(ValueError, 'actively tracked'):
            c.solve(self.before, self.quest, self.after)

    def test_rejects_changed_generation_and_profile(self):
        for row in self.after:
            row['identity'][2] = 2
        with self.assertRaisesRegex(ValueError, 'generation changed'):
            c.solve(self.before, self.quest, self.after)
        self.after = self.samples(self.pposes, 'pc', offset=10_000_000_000)
        for row in self.quest:
            row['devices']['left_hand']['interaction_profile'] = 'different'
        with self.assertRaisesRegex(ValueError, 'profiles differ'):
            c.solve(self.before, self.quest, self.after)

    def test_rejects_duplicate_timestamps_and_nonfinite(self):
        self.quest[1]['sample_time_ns'] = self.quest[0]['sample_time_ns']
        with self.assertRaisesRegex(ValueError, 'increasing sample timestamps'):
            c.solve(self.before, self.quest, self.after)
        self.quest = self.samples(self.qposes, 'quest')
        self.quest[0]['devices']['right_hand']['position'][0] = float('nan')
        with self.assertRaises(ValueError):
            c.solve(self.before, self.quest, self.after)


if __name__ == '__main__':
    unittest.main()
