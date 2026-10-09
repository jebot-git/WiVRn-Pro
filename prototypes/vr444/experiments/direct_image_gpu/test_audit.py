import copy
import unittest
from audit import audit, MODES, TIMES


def valid_result():
    data = dict(schema=1, experiment="direct-image-gpu", headset_used=False,
                format_list=False, warmups=1, repetitions=1, cases=[],
                promotion_blocked=True, **{"pass": True})
    for w, h in ((1, 1), (1, 17), (17, 1), (17, 19), (31, 33), (65, 47), (256, 2)):
        for kind in range(6):
            for q in (1, 8):
                index = len(data["cases"])
                rows = []
                for order in range(3):
                    rows.append(dict(rep=0, order=order, mode=MODES[(index+1+order)%3],
                                     bytes_exact=True, one_eotf=True, sampled_baseline_exact=True,
                                     malformed_pair_rejected_before_submit=True,
                                     max_linear_error=.0015, max_srgb_code_error=.25,
                                     **{key: .1 for key in TIMES}))
                data["cases"].append(dict(name=f"{w}x{h}-pattern{kind}-q{q}", device="test device",
                                          device_type=2, input_memory_flags=14, iterations=rows))
    data["case_count"] = len(data["cases"])
    return data


class AuditTests(unittest.TestCase):
    def test_pass_preserves_limits(self):
        result = audit(valid_result())
        self.assertEqual(result["case_count"], 84)
        self.assertFalse(result["performance_claim"])
        self.assertTrue(result["promotion_blocked"])

    def test_software_explicit(self):
        data = valid_result()
        for case in data["cases"]:
            case["device_type"] = 4
        self.assertTrue(audit(data)["software"])

    def test_tampered_evidence_rejected(self):
        mutations = [
            lambda d: d.update(case_count=83),
            lambda d: d.update(headset_used=True),
            lambda d: d.update(promotion_blocked=False),
            lambda d: d["cases"].pop(),
            lambda d: d["cases"][0].update(name=d["cases"][1]["name"]),
            lambda d: d["cases"][0].update(name="missing-required-fixture"),
            lambda d: d["cases"][0]["iterations"].reverse(),
            lambda d: d["cases"][0]["iterations"][0].update(bytes_exact=False),
            lambda d: d["cases"][0]["iterations"][0].update(sampled_baseline_exact=False),
            lambda d: d["cases"][0]["iterations"][0].update(stereo_gpu_ms=float("nan")),
            lambda d: d["cases"][0]["iterations"][0].update(oracle_ms=float("inf")),
            lambda d: d["cases"][0]["iterations"][0].update(max_srgb_code_error=.5),
            lambda d: d["cases"][0].update(input_memory_flags=2),
        ]
        base = valid_result()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(base)
                mutation(changed)
                with self.assertRaises(ValueError):
                    audit(changed)


if __name__ == "__main__":
    unittest.main()
