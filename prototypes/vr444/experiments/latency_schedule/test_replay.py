import unittest
from dataclasses import replace
from replay import DeadlinePairs, Eye, Pair, frontier, parse_present


class DeadlineTests(unittest.TestCase):
    def pair(self, model, frame, ready=10, deadline=20, warp=42):
        for eye in (0, 1):
            self.assertTrue(model.offer(Eye(frame, warp, eye, ready, deadline)))

    def test_no_half_or_future_publication(self):
        model = DeadlinePairs()
        model.offer(Eye(1, 42, 0, 10, 20))
        self.assertIsNone(model.select(10))
        model.offer(Eye(1, 42, 1, 15, 20))
        self.assertIsNone(model.select(14))
        pair = model.select(15)
        self.assertEqual(pair[0].identity, pair[1].identity)
        self.assertEqual([x.eye for x in pair], [0, 1])

    def test_deadline_inclusive_then_expired(self):
        model = DeadlinePairs()
        self.pair(model, 1)
        self.assertIsNotNone(model.select(20))
        self.pair(model, 2)
        self.assertIsNone(model.select(21))

    def test_identity_and_deadline_mismatch_poison_frame(self):
        base = Eye(1, 42, 1, 10, 20)
        for bad in (replace(base, warp=43), replace(base, width=640),
                    replace(base, height=576), replace(base, kind="raw"),
                    replace(base, deadline=21)):
            model = DeadlinePairs()
            model.offer(replace(base, eye=0))
            self.assertFalse(model.offer(bad))
            self.assertFalse(model.offer(base))
            self.assertIsNone(model.select(10))

    def test_duplicate_stale_and_non_regression(self):
        model = DeadlinePairs()
        self.pair(model, 2)
        self.assertFalse(model.offer(Eye(2, 42, 0, 10, 20)))
        model.select(10)
        self.assertFalse(model.offer(Eye(1, 42, 0, 10, 20)))
        self.assertFalse(model.offer(Eye(2, 42, 1, 10, 20)))

    def test_newest_complete_not_newest_orphan(self):
        model = DeadlinePairs()
        self.pair(model, 1)
        self.pair(model, 2)
        model.offer(Eye(3, 42, 0, 10, 20))
        self.assertEqual(model.select(10)[0].frame, 2)
        self.assertEqual(set(model.pending), {3})

    def test_bound_protects_complete_under_many_orphans(self):
        model = DeadlinePairs()
        self.pair(model, 1)
        for frame in range(2, 1000):
            model.offer(Eye(frame, 42, 0, 10, 20))
            self.assertLessEqual(len(model.pending), 2)
            self.assertLessEqual(sum(map(len, model.pending.values())), 4)
            self.assertIn(1, model.pending)
        self.assertEqual(model.select(10)[0].frame, 1)

    def test_ready_after_deadline_never_selectable(self):
        model = DeadlinePairs()
        self.pair(model, 1, ready=21, deadline=20)
        for now in (19, 20, 21, 100):
            self.assertIsNone(model.select(now))

    def test_invalid_types_and_geometry(self):
        base = Eye(1, 42, 0, 10, 20)
        for bad in (replace(base, eye=True), replace(base, eye=2),
                    replace(base, frame=-1), replace(base, ready=1.5),
                    replace(base, width=0), replace(base, deadline=True)):
            with self.assertRaises(ValueError):
                DeadlinePairs().offer(bad)


class ReplayTests(unittest.TestCase):
    def row(self, **kwargs):
        row = dict(left=1, right=1, left_warp=9, right_warp=9,
                   left_receive_xr_ns=10, right_receive_xr_ns=11,
                   left_decode_ready_xr_ns=20, right_decode_ready_xr_ns=21,
                   submit_xr_ns=30, predicted_display_xr_ns=40)
        row.update(kwargs)
        return row

    def test_exact_identity_rejects_cross_eye_and_changed_repeat(self):
        for changed in (dict(right=2), dict(right_warp=10), dict(left=True)):
            with self.assertRaises(ValueError):
                parse_present([self.row(**changed)])
        with self.assertRaises(ValueError):
            parse_present([self.row(), self.row(submit_xr_ns=31, left_decode_ready_xr_ns=22)])

    def test_time_order_rejection(self):
        for changed in (dict(left_receive_xr_ns=22), dict(right_decode_ready_xr_ns=31),
                        dict(submit_xr_ns=0)):
            with self.assertRaises(ValueError):
                parse_present([self.row(**changed)])
        with self.assertRaises(ValueError):
            parse_present([self.row(), self.row()])

    def test_predicted_time_is_not_a_measured_deadline(self):
        pairs, events = parse_present([self.row(predicted_display_xr_ns=1)])
        self.assertEqual(len(pairs), 1)
        self.assertGreater(events[0][0], events[0][1])

    def test_frontier_does_not_choose_future_pair(self):
        a, b = Pair(1, 9, (10, 11), (20, 21)), Pair(2, 10, (25, 26), (35, 36))
        r = frontier([a, b], [(30, 40, a)], 0)
        self.assertEqual(r["ahead"], 0)
        self.assertEqual(r["changed"], 0)

    def test_margin_changes_eligibility_and_ttl_repeats_can_still_be_stale(self):
        a, b = Pair(1, 9, (10, 11), (20, 21)), Pair(2, 10, (25, 26), (29, 29))
        self.assertEqual(frontier([a, b], [(30, 40, a)], 0)["ahead"], 1)
        self.assertEqual(frontier([a, b], [(30, 40, a)], 2)["ahead"], 0)
        r = frontier([a], [(22, 40, a), (100, 110, a)], 0, 20)
        self.assertEqual(r["stale_repeats"], 1)
        self.assertEqual(r["empty"], 0)


if __name__ == "__main__":
    unittest.main()
