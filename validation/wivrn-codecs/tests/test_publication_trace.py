import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

from test_candidate_trace import fixture as candidate_fixture

spec = importlib.util.spec_from_file_location('publication_trace', Path(__file__).resolve().parents[1] / 'integration/publication_trace.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    trace, presented = candidate_fixture()
    ledger = [dict(kind='header', schema=1, capacity=16384, clock='client_xr_ns', coverage=module.COVERAGE),
              dict(kind='reset', sequence=1, epoch=1)]
    history = [dict(decoder=i // 3, slot=i % 3, present=False) for i in range(9)]
    for eye in range(2):
        for slot in range(2):
            added = {k: v for k, v in trace['history'][eye * 3 + slot].items() if k not in ('decoder', 'slot')}
            ledger.append(dict(kind='publish', sequence=len(ledger), epoch=1, decoder=eye, added=added, evicted=dict(present=False)))
            history[eye * 3 + slot + 1] = added | dict(decoder=eye, slot=slot + 1)
    trace['history'] = copy.deepcopy(history)
    ledger.append(dict(kind='checkpoint', selection_sequence=1, prefix_records=5, epoch=1, dropped=0, valid=True, history=history))
    return ledger, [trace], [presented]


class PublicationTraceTests(unittest.TestCase):
    def test_exact_prefix_reconstructs_all_history_and_keeps_tail_unqualified(self):
        result = module.evaluate(*fixture())
        self.assertEqual(result['status'], 'pass', result['errors'])
        self.assertEqual((result['events'], result['publication_events'], result['checkpoints']), (5, 4, 1))
        self.assertEqual(result['published_eye_identities_absent_all_candidate_snapshots'], 0)
        self.assertFalse(result['complete_process_tail'])
        self.assertFalse(result['promotion_eligible'])

    def test_replay_counts_distinct_published_eye_identities(self):
        ledger, traces, _ = fixture()
        self.assertEqual(module.replay(ledger, traces)['distinct_published_eye_identities'], 4)

    def test_same_frame_cannot_change_metadata_within_an_epoch(self):
        ledger, traces, _ = fixture()
        checkpoint = ledger.pop()
        changed = copy.deepcopy(ledger[2]['added'])
        changed['warp'] += 1
        ledger.append(dict(kind='publish', sequence=6, epoch=1, decoder=0, added=changed, evicted=dict(present=False)))
        checkpoint['prefix_records'] = 6
        ledger.append(checkpoint)
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            module.replay(ledger, traces)

    def test_lost_invalid_or_foreign_epoch_checkpoint_fails(self):
        for key, value in [('dropped', 1), ('dropped', True), ('valid', False), ('valid', 1),
                           ('epoch', 2), ('prefix_records', 4), ('selection_sequence', 2)]:
            with self.subTest(key=key, value=value):
                ledger, traces, presented = fixture()
                ledger[-1][key] = value
                self.assertEqual(module.evaluate(ledger, traces, presented)['status'], 'fail')

    def test_event_corruption_order_and_capacity_fail_closed(self):
        for mutation in range(14):
            ledger, traces, presented = fixture()
            if mutation == 0: del ledger[1]
            elif mutation == 1: ledger.insert(2, copy.deepcopy(ledger[2]))
            elif mutation == 2: ledger[2]['epoch'] = 2
            elif mutation == 3: ledger[2]['sequence'] = True
            elif mutation == 4: ledger[0]['capacity'] = 4
            elif mutation == 5: ledger[2]['decoder'] = 3
            elif mutation == 6: ledger[2]['added']['warp'] = 2**64
            elif mutation == 7: ledger[2]['added']['ready_xr_ns'] = 9
            elif mutation == 8: ledger[2]['evicted'] = copy.deepcopy(ledger[2]['added'])
            elif mutation == 9: ledger[2]['evicted']['frame'] = 0
            elif mutation == 10: ledger[2]['added']['present'] = False
            elif mutation == 11: ledger[2]['added']['alpha'] = 0
            elif mutation == 12: ledger[4]['added']['ready_xr_ns'] = 19
            elif mutation == 13: ledger.append(copy.deepcopy(ledger[2]))
            with self.subTest(mutation=mutation):
                self.assertEqual(module.evaluate(ledger, traces, presented)['status'], 'fail')

    def test_omitted_and_fabricated_snapshot_history_fails(self):
        for mutation in range(5):
            ledger, traces, presented = fixture()
            if mutation == 0: ledger[-1]['history'][1]['ready_xr_ns'] += 1
            elif mutation == 1: ledger[-1]['history'][0]['slot'] = 1
            elif mutation == 2: ledger[-1]['history'].pop()
            elif mutation == 3: ledger[-1]['history'][1]['unknown'] = 0
            elif mutation == 4: traces[0]['history'][1]['ready_xr_ns'] += 1
            with self.subTest(mutation=mutation):
                self.assertEqual(module.evaluate(ledger, traces, presented)['status'], 'fail')

    def test_unknown_missing_header_and_checkpoint_fields_fail(self):
        for index in (0, 1, 2, -1):
            for action in ('extra', 'missing'):
                ledger, traces, presented = fixture()
                if action == 'extra': ledger[index]['extra'] = 1
                else: ledger[index].pop(next(iter(ledger[index])))
                self.assertEqual(module.evaluate(ledger, traces, presented)['status'], 'fail')

    def test_metadata_and_ready_clock_validation_covers_evicted_unobserved_events(self):
        ledger, traces, _ = fixture()
        # Add an eye publication then evict it before the first snapshot. Its
        # timestamp must still precede that selection, and metadata cannot vary.
        checkpoint = ledger.pop()
        slots = copy.deepcopy(checkpoint['history'])
        for number in (3, 4, 5):
            added = copy.deepcopy(ledger[2]['added']) | dict(frame=number, warp=number + 10, view_display_time_xr_ns=120 + number)
            evicted = {k: v for k, v in slots[0].items() if k not in ('decoder', 'slot')}
            ledger.append(dict(kind='publish', sequence=len(ledger), epoch=1, decoder=0, added=added, evicted=evicted))
            eye = [dict(item, slot=index) for index, item in enumerate(slots[1:3] + [added | dict(decoder=0)])]
            slots[:3] = eye
        checkpoint['prefix_records'] = len(ledger) - 1
        checkpoint['history'] = slots
        ledger.append(checkpoint)
        traces[0]['history'] = copy.deepcopy(slots)
        result = module.replay(ledger, traces)
        self.assertEqual(result['published_eye_identities_absent_all_candidate_snapshots'], 2)
        changed = copy.deepcopy(ledger)
        changed[2]['added']['ready_xr_ns'] = 60
        with self.assertRaises(ValueError): module.replay(changed, traces)

    def test_reset_epoch_can_reuse_identity_without_reusing_old_history(self):
        ledger, traces, _ = fixture()
        # Standalone replay permits epochs; full candidate validation remains an
        # independent constraint and is never weakened for reset diagnostics.
        ledger = ledger[:-1] + [dict(kind='reset', sequence=6, epoch=2)]
        for eye in range(2):
            for slot in range(2):
                item = copy.deepcopy(ledger[2 + eye * 2 + slot])
                item.update(sequence=len(ledger), epoch=2)
                ledger.append(item)
        checkpoint = fixture()[0][-1]
        checkpoint.update(prefix_records=10, epoch=2)
        ledger.append(checkpoint)
        self.assertEqual(module.replay(ledger, traces)['last_decoder_epoch'], 2)
        ledger[-1]['epoch'] = 1
        with self.assertRaises(ValueError): module.replay(ledger, traces)

    def test_outer_candidate_failure_is_not_a_publication_pass(self):
        ledger, traces, presented = fixture()
        presented[0]['right_warp'] += 1
        self.assertEqual(module.evaluate(ledger, traces, presented)['status'], 'fail')

    def test_disabled_and_duplicate_json_are_rejected(self):
        self.assertEqual(module.evaluate([], [], [], enabled=False)['status'], 'off')
        self.assertEqual(module.evaluate(*fixture(), enabled=False)['status'], 'fail')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'duplicate.jsonl'
            path.write_text('{"kind":"header","kind":"checkpoint"}\n')
            with self.assertRaises(ValueError): module.load(path)
