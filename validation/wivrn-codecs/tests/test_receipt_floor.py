import copy
import importlib.util
from pathlib import Path
import random
import unittest

spec=importlib.util.spec_from_file_location('receipt_floor_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/receipt_floor.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def eye(receipt,delay):
    return dict(present=True,receive_xr_ns=receipt,ready_xr_ns=receipt+delay)


class ReceiptFloorTests(unittest.TestCase):
    def test_all_eyes_and_alpha_have_their_own_receipt_floor(self):
        known={1:dict(pair=[eye(100,10),eye(107,18_000_000),eye(121,50_000_000)])}
        proof=module.floor_proof(known)
        self.assertEqual(proof['maximum_observed_eye_receive_to_ready_ns'],50_000_000);self.assertEqual(proof['participating_eyes'],3)
        self.assertTrue(proof['every_participating_eye_equals_own_original_receipt'])

    def test_floor_dominates_every_bounded_per_eye_improvement(self):
        rng=random.Random(81543)
        for _ in range(1024):
            pair=[eye(rng.randrange(1,10**12),rng.randrange(50_000_001)) for _ in range(rng.randrange(2,4))]
            known={1:dict(pair=pair)};before=copy.deepcopy(known)
            proof=module.floor_proof(known)
            floor=max(e['receive_xr_ns'] for e in pair)
            arbitrary=max(e['receive_xr_ns']+rng.randrange(e['ready_xr_ns']-e['receive_xr_ns']+1) for e in pair)
            self.assertLessEqual(floor,arbitrary);self.assertEqual(known,before)
            self.assertEqual(floor,max(e['receive_xr_ns'] for e in pair))

    def test_invalid_receipt_or_readiness_fails_closed(self):
        for receipt,ready in ((True,9),(0,9),(20,19),(2**63,2**63)):
            pair=[dict(present=True,receive_xr_ns=receipt,ready_xr_ns=ready)]
            with self.subTest(receipt=receipt,ready=ready),self.assertRaises(ValueError):module.floor_proof({1:dict(pair=pair)})
        for known in ({},{1:dict(pair=[dict(present=False)])}):
            with self.assertRaises(ValueError):module.floor_proof(known)

    def test_already_immediate_and_mode_controls(self):
        proof=module.floor_proof({1:dict(pair=[eye(1,0),eye(2,0),dict(present=False)])})
        self.assertEqual(proof['maximum_observed_eye_receive_to_ready_ns'],0)
        for flag,advance in ((1,0),(True,1),(True,8_000_000)):
            with self.assertRaises(ValueError):module.quantile.evaluate([],[],[],{},ready_at_receipt=flag,publication_advance_ns=advance)
