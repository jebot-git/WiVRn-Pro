"""Synthetic accounting negatives; no Vulkan or Android execution."""
import copy
import unittest
import audit as a


class Tests(unittest.TestCase):
    def example(self, modern=False):
        return dict(schema=1, **{'pass':True}, device='Adreno (TM) 650', timeline_enabled=True,
                    synchronization2_enabled=modern, slots=3, offered_pairs=512, decoded_pairs=80,
                    consumed_pairs=51, verified_pairs=51, pressure_drops=432,
                    unacquired_completed_drops=29, slot_reuses=77, headset_used=False,
                    performance_claim=False,
                    scope='Private queue buffer identity/lifetime model; no images, codec, XR or presentation latency')

    def test_both_paths(self):
        for modern in (False, True):
            result = a.accounting(self.example(modern), modern)
            self.assertTrue(result['accounting_exact'])
            self.assertEqual(result['synchronization2_enabled_and_used'], modern)

    def test_missing_counters(self):
        for field in ('decoded_pairs','consumed_pairs','verified_pairs','pressure_drops',
                      'unacquired_completed_drops','slot_reuses'):
            d = self.example(); del d[field]
            with self.assertRaises(ValueError): a.accounting(d, False)

    def test_counter_types_and_bounds(self):
        for value in (True, -1, 513, 80.0, None):
            d = self.example(); d['decoded_pairs'] = value
            with self.assertRaises(ValueError): a.accounting(d, False)

    def test_complete_accounting(self):
        for field in ('decoded_pairs','consumed_pairs','verified_pairs','pressure_drops',
                      'unacquired_completed_drops','slot_reuses'):
            d = self.example(); d[field] += 1
            with self.assertRaises(ValueError): a.accounting(d, False)

    def test_exact_scope(self):
        for field, value in (('pass',False), ('timeline_enabled',False), ('synchronization2_enabled',True),
                             ('slots',4), ('offered_pairs',511), ('headset_used',True),
                             ('performance_claim',True), ('device','other GPU'), ('scope','images tested')):
            d = self.example(); d[field] = value
            with self.assertRaises(ValueError): a.accounting(d, False)

    def test_unexercised_model_rejected(self):
        for values in (dict(decoded_pairs=3,consumed_pairs=3,verified_pairs=3,pressure_drops=509,
                            unacquired_completed_drops=0,slot_reuses=0),
                       dict(decoded_pairs=512,consumed_pairs=512,verified_pairs=512,pressure_drops=0,
                            unacquired_completed_drops=0,slot_reuses=509)):
            d=self.example(); d.update(values)
            with self.assertRaises(ValueError): a.accounting(d, False)


if __name__ == '__main__': unittest.main()
