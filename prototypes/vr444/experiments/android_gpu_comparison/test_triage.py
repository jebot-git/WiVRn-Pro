"""Adversarial audit checks use synthetic copies; never create device evidence."""
import copy
import unittest
import triage as t


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = t.smoke.blob(t.ROOT, t.SMOKE_PIN,
                          t.PREFIX+'headset_review_smoke/results/device-run/gpu-two-profiles.stdout')
        cls.original = t.parse(raw)
        (cls.rrf, cls.rrb), cls.bindings = t.manifests(t.ROOT)

    def fixture(self):
        data = copy.deepcopy(self.original)
        data['repetitions'] = 7
        for index, case in enumerate(data['cases']):
            by_arm = {row['mode']: row for row in case['iterations']}
            rows = []
            for rep in range(7):
                for order in range(9):
                    arm = t.consumer.ARMS[(index+rep+3+order)%9]
                    row = copy.deepcopy(by_arm[arm])
                    row.update(rep=rep, order=order)
                    rows.append(row)
            case['iterations'] = rows
        return data

    def test_synthetic_valid_shape(self):
        result = t.analyze(self.fixture(), self.rrf, self.rrb)
        self.assertEqual(result['recorded_invocations'], 126)
        self.assertEqual(result['omission_controls'], 18)
        self.assertEqual(len(result['profiles']), 2)
        self.assertEqual(len(result['profiles'][0]['comparisons']), 8)

    def test_old_smoke_cannot_be_relabelled(self):
        with self.assertRaises(ValueError): t.analyze(self.original, self.rrf, self.rrb)

    def test_exact_order_and_population(self):
        for edit in (lambda d: d['cases'].reverse(), lambda d: d['cases'].pop(),
                     lambda d: d['cases'].append(copy.deepcopy(d['cases'][0]))):
            data = self.fixture(); edit(data)
            with self.assertRaises(ValueError): t.analyze(data, self.rrf, self.rrb)

    def test_missing_or_duplicate_arm(self):
        for edit in (lambda rows: rows.pop(), lambda rows: rows.__setitem__(0, copy.deepcopy(rows[1]))):
            data = self.fixture(); edit(data['cases'][0]['iterations'])
            with self.assertRaises(ValueError): t.analyze(data, self.rrf, self.rrb)

    def test_oracle_and_omission_gates(self):
        for key in ('image_bytes_exact', 'first_consumer_samples_exact', 'first_consumer_saved_before_oracle',
                    'right_consumer_omission_rejected', 'wrong_shape_rejected_before_write_or_submit'):
            data = self.fixture(); data['cases'][0]['iterations'][0][key] = False
            with self.assertRaises(ValueError): t.analyze(data, self.rrf, self.rrb)

    def test_clock_and_memory_mutations(self):
        for key, value in (('joint_outer_wall_ms', 0), ('joint_outer_wall_ms', float('inf')),
                           ('direct_decode_ms', -1), ('input_memory_flags', [2, 2]),
                           ('timed_queue_submissions', 1), ('rep', True), ('order', 8)):
            data = self.fixture(); data['cases'][0]['iterations'][0][key] = value
            with self.assertRaises(ValueError): t.analyze(data, self.rrf, self.rrb)

    def test_wire_population_and_size(self):
        data = self.fixture(); data['cases'][0]['variants'][1]['eye_wire_bytes'][1] += 1
        with self.assertRaises(ValueError): t.analyze(data, self.rrf, self.rrb)

    def test_arm_variant_cannot_be_changed(self):
        data = self.fixture(); data['cases'][0]['iterations'][0]['variant'] = 2
        with self.assertRaises(ValueError): t.analyze(data, self.rrf, self.rrb)

    def test_plan_frozen(self):
        plan = t.plan_for(t.ROOT)
        self.assertEqual(len(plan['files']), 39)
        self.assertEqual([r['name'] for r in plan['runs']], ['extensions','memory',*t.LABELS])
        for run in plan['runs'][2:]:
            self.assertEqual(run['argv'][-4:], ['--device','0','--reps','7'])
            self.assertEqual(run['timeout_s'], 180)

    def test_manifest_filters_keep_both_eyes_and_partitions(self):
        self.assertEqual(len(self.rrf['cases']), 4)
        self.assertEqual(len(self.rrb['cases']), 16)
        self.assertEqual({r['q'] for r in self.rrf['cases']}, {1, 8})
        t.consumer.matched_variants(self.rrf, self.rrb)

    def test_ratio_components_are_not_added_quantiles(self):
        result = t.analyze(self.fixture(), self.rrf, self.rrb)
        case = result['profiles'][0]
        direct = case['arm_components']['rrf_direct_default']['joint_outer_wall_ms']['median']
        staged = case['arm_components']['rrf_staged_default']['joint_outer_wall_ms']['median']
        self.assertEqual(case['comparisons'][0]['fields']['joint_outer_wall_ms']['ratio_of_medians'], direct/staged)


if __name__ == '__main__': unittest.main()
