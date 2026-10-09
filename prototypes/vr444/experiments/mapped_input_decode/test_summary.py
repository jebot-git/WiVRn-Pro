import copy
import statistics
import unittest
from summarize import comparisons, summarize


def report():
    names = ['rrf_cpu_reference'] + [f'{kind}_{path}_{memory}' for kind in ('rrf', 'rrb32')
                                    for memory in ('default', 'cached') for path in ('staged', 'direct')]
    rows = []
    for index in range(26):
        name = f'corpus/{"captured" if index < 12 else "synthetic"}/{index}'
        arms = {arm: {'outer_wall': {'p50_ms': 1, 'p95_ms': 2}} for arm in names}
        if index < 12:
            arms['rrf_staged_default']['outer_wall']['p50_ms'] = [1, 2, 10][index % 3]
            arms['rrf_direct_default']['outer_wall']['p50_ms'] = [11, 3, 4][index % 3]
        rows.append(dict(name=name, arms=arms))
    conditions = {key: True for key in ('research_teams_confirmed_idle', 'manifests_unchanged',
                                       'corpora_unchanged', 'binary_unchanged', 'sources_unchanged', 'shaders_unchanged')}
    conditions.update(repetitions=31, exit_code=0, manifest_sha256=['a', 'b'], binary_sha256='c',
                      source_sha256={'source': 'd'}, shader_sha256={'shader': 'e'})
    return dict(device=['example', 2, 123], pass_=True, qualification_only=False, case_count=26,
                measured_stereo_arm_invocations=7254, headset_used=False, promotion_blocked=True,
                conditions=conditions, per_case=rows, **{'pass': True})


class SummaryTests(unittest.TestCase):
    def test_paired_difference_is_not_difference_of_group_medians(self):
        result = comparisons(report())[0]['paired_differences']['p50_ms']
        self.assertEqual(result['median_candidate_minus_baseline_ms'], 1)
        self.assertNotEqual(result['median_candidate_minus_baseline_ms'],
                            statistics.median([11, 3, 4] * 4) - statistics.median([1, 2, 10] * 4))
        self.assertEqual((result['improved_pairs'], result['regressed_pairs']), (4, 8))

    def test_omitted_synthetic_and_duplicated_capture_reject(self):
        for change in ('missing', 'duplicate'):
            value = report()
            if change == 'missing': value['per_case'].pop()
            else: value['per_case'][1] = copy.deepcopy(value['per_case'][0])
            with self.subTest(change=change), self.assertRaises(ValueError):
                summarize([value])

    def test_conditions_and_device_identity_reject(self):
        first = report()
        self.assertEqual(len(summarize([first])['measurements']), 1)
        with self.assertRaises(ValueError): summarize([first, copy.deepcopy(first)])
        for key in ('sources_unchanged', 'corpora_unchanged', 'research_teams_confirmed_idle'):
            bad = report(); bad['conditions'][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError): summarize([bad])
        second = report(); second['device'][0] = 'other'; second['conditions']['binary_sha256'] = 'changed'
        with self.assertRaises(ValueError): summarize([first, second])


if __name__ == '__main__':
    unittest.main()
