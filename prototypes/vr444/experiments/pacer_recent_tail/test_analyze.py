import copy,importlib.util
from pathlib import Path
import unittest
s=importlib.util.spec_from_file_location('tail',Path(__file__).with_name('analyze.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def fixture():
 h={**m.HEADER,'window_frames':900}
 r=dict(type='update',sequence=1,pacer_id=1,epoch=0,batch=1,boundary_frame=100,newest_feedback_frame=100,oldest_recent_frame=0,
 previous_newest_frame=-1,legacy_count=101,recent_count=101,legacy_tail_ns=40_000_000,recent_tail_ns=40_000_000,margin_ns=1_000_000,
 previous_budget_ns=0,applied_budget_ns=41_000_000,valid=True,stale_epoch=False,recent_used=False,slew_limited=False,decrease_blocked=False)
 a={**r,'sequence':2,'batch':2,'boundary_frame':2000,'newest_feedback_frame':2000,'oldest_recent_frame':1101,
 'previous_newest_frame':100,'legacy_count':2000,'recent_count':900,'recent_tail_ns':8_000_000,'previous_budget_ns':41_000_000,
 'applied_budget_ns':40_000_000,'recent_used':True,'slew_limited':True}
 return [h,r,a,dict(type='footer',records=2,dropped=0,complete=True)]
class TailAudit(unittest.TestCase):
 def test_supported_slew_and_sparse_fallback(self):
  r=m.evaluate(fixture());self.assertEqual(r['counts'],dict(recent=1,fallback=1,slew=1));self.assertFalse(r['raw_feedback_quantiles_recomputed']);self.assertFalse(r['promotion_eligible'])
 def test_rehashed_semantics_reject(self):
  for k,v in [('sequence',True),('batch',1),('newest_feedback_frame',2001),('oldest_recent_frame',1100),('recent_count',901),('recent_used',False),('margin_ns',-1),('applied_budget_ns',9_000_000),('previous_budget_ns',42_000_000),('previous_newest_frame',99),('stale_epoch',True),('valid',False),('slew_limited',False)]:
   with self.subTest(k=k):
    rows=fixture();rows[2][k]=v
    with self.assertRaises(ValueError):m.evaluate(rows)
 def test_subset_tail_can_exceed_legacy_tail(self):
  rows=fixture();rows[2].update(recent_tail_ns=60_000_000,applied_budget_ns=61_000_000,slew_limited=False);self.assertEqual(m.evaluate(rows)['status'],'pass')
 def test_epoch_reset_retains_budget_but_clears_watermark(self):
  rows=fixture();rows[2].update(epoch=1,previous_newest_frame=-1);self.assertEqual(m.evaluate(rows)['status'],'pass')
  rows[2]['previous_newest_frame']=100
  with self.assertRaises(ValueError):m.evaluate(rows)
 def test_complete_trace_and_typed_header(self):
  for mutate in [lambda r:r[-1].update(dropped=1),lambda r:r[-1].update(complete=False),lambda r:r[0].update(schema=True),lambda r:r[0].update(window_frames=True),lambda r:r[-1].update(records=1),lambda r:r[1].update(extra=0)]:
   rows=fixture();mutate(rows)
   with self.assertRaises(ValueError):m.evaluate(rows)


def rolling_fixture():
 rows=fixture();rows[0]={**m.ROLLING_HEADER,'window_frames':900}
 for i,r in enumerate(rows[1:-1],1):
  r.update(copy_begin_host_ns=i*100,copy_end_host_ns=i*100+10,worker_apply_host_ns=i*100+20,
           consumed_snapshots=i,coalesced_snapshots=0,empty_snapshots=0)
 rows[-1].update(produced_snapshots=2,consumed_snapshots=2,coalesced_snapshots=0,empty_snapshots=0,last_consumed_batch=2,unconsumed_snapshots=0)
 return rows


class RollingTailAudit(unittest.TestCase):
 def test_mode_and_accounting_are_explicit(self):
  r=m.evaluate(rolling_fixture());self.assertEqual(r['feedback_bank'],'rolling');self.assertTrue(r['all_generated_snapshots_consumed']);self.assertFalse(r['eye_pair_completeness_verified']);self.assertFalse(r['feedback_loss_measured'])
  self.assertEqual(r['copy_duration_host_ns'],dict(min=10,max=10));self.assertEqual(r['copy_end_to_apply_host_ns'],dict(min=10,max=10))
 def test_coalescing_empty_reset_and_terminal_pending_are_not_hidden(self):
  rows=rolling_fixture();rows[1].update(batch=3,coalesced_snapshots=2);rows[2].update(batch=5,consumed_snapshots=3,coalesced_snapshots=2,empty_snapshots=1)
  rows[-1].update(produced_snapshots=7,consumed_snapshots=4,coalesced_snapshots=2,empty_snapshots=2,last_consumed_batch=6,unconsumed_snapshots=1)
  r=m.evaluate(rows);self.assertFalse(r['all_generated_snapshots_consumed']);self.assertEqual(r['snapshot_accounting']['unconsumed_snapshots'],1)
 def test_forged_mode_clock_counts_and_complete_eye_claim_reject(self):
  mutations=[lambda r:r[0].update(feedback_bank='alternating'),lambda r:r[0].update(eye_pair_completeness_recorded=True),lambda r:r[0].update(snapshot_slots=4999),lambda r:r[1].update(copy_begin_host_ns=True),lambda r:r[1].update(worker_apply_host_ns=109),lambda r:r[2].update(copy_begin_host_ns=109),lambda r:r[2].update(coalesced_snapshots=1),lambda r:r[2].update(empty_snapshots=1),lambda r:r[-1].update(produced_snapshots=3),lambda r:r[-1].update(empty_snapshots=True),lambda r:r[-1].update(consumed_snapshots=3),lambda r:r[2].update(pacer_id=2)]
  for mutate in mutations:
   with self.subTest(mutate=mutate):
    rows=rolling_fixture();mutate(rows)
    with self.assertRaises(ValueError):m.evaluate(rows)

if __name__ == '__main__':unittest.main()
