#!/usr/bin/env python3
"""Fail-closed arithmetic/coverage audit of opt-in pacer-tail summary telemetry."""
import argparse,hashlib,json
from pathlib import Path
HEADER={'type':'header','schema':1,'minimum_samples':128,'quantile_numerator':995,
 'quantile_denominator':1000,'downward_step_ns':1000000,'capacity':4096,
 'diagnostic_only':True,'raw_feedback_recorded':False}
INTS={'sequence','pacer_id','epoch','batch','boundary_frame','newest_feedback_frame',
 'oldest_recent_frame','previous_newest_frame','legacy_count','recent_count',
 'legacy_tail_ns','recent_tail_ns','margin_ns','previous_budget_ns','applied_budget_ns'}
BOOLS={'stale_epoch','valid','recent_used','slew_limited','decrease_blocked'}
ROLLING_HEADER={**HEADER,'schema':2,'feedback_bank':'rolling','clock':'host_monotonic_ns',
 'snapshot_slots':5000,'eye_pair_completeness_recorded':False,'coverage':'consumed_worker_snapshots'}
ROLLING_INTS={'copy_begin_host_ns','copy_end_host_ns','worker_apply_host_ns',
 'consumed_snapshots','coalesced_snapshots','empty_snapshots'}
ROLLING_FOOTER={'produced_snapshots','consumed_snapshots','coalesced_snapshots','empty_snapshots',
 'last_consumed_batch','unconsumed_snapshots'}
def need(x,m):
 if not x:raise ValueError(m)
def evaluate(rows):
 need(len(rows)>=3 and all(type(r)is dict for r in rows),'complete nonempty objects')
 h,f=rows[0],rows[-1];rolling=h.get('schema')==2;header=ROLLING_HEADER if rolling else HEADER
 need(set(h)==set(header)|{'window_frames'},'header fields')
 need(all(type(h[k])is type(v) and h[k]==v for k,v in header.items()),'header contract')
 window=h['window_frames'];need(type(window)is int and 360<=window<=1800,'window bound')
 need(set(f)==({'type','records','dropped','complete'}|(ROLLING_FOOTER if rolling else set())) and f['type']=='footer' and f['complete']is True and type(f['records'])is int and f['records']==len(rows)-2 and type(f['dropped'])is int and f['dropped']==0,'footer or loss')
 need(f['records']<=4096,'capacity');last={};counts={'recent':0,'fallback':0,'slew':0};copy_times=[];apply_delays=[];feedback_lags=[];previous_snapshot=None
 if rolling:
  need(all(type(f[k])is int and 0<=f[k]<2**63 for k in ROLLING_FOOTER),'typed snapshot footer')
  need(f['consumed_snapshots']==f['records']+f['empty_snapshots'] and f['last_consumed_batch']==f['consumed_snapshots']+f['coalesced_snapshots'] and f['produced_snapshots']==f['last_consumed_batch']+f['unconsumed_snapshots'],'snapshot footer accounting')
 for i,r in enumerate(rows[1:-1],1):
  need(set(r)==INTS|BOOLS|{'type'}|(ROLLING_INTS if rolling else set()) and r['type']=='update','update fields')
  need(all(type(r[k])is int and -1<=r[k]<2**63 for k in INTS) and all(type(r[k])is bool for k in BOOLS),'typed fields')
  need(r['sequence']==i and r['pacer_id']>0 and r['epoch']>=0 and r['batch']>0,'record identity')
  need(r['valid'] and not r['stale_epoch'],'invalid or reset-raced update; preserve capture failure')
  n,old,prior=r['newest_feedback_frame'],r['oldest_recent_frame'],r['previous_newest_frame']
  need(0<=old<=n<=r['boundary_frame'] and -1<=prior<=n and old>=max(0,n-window+1),'frame-age range')
  need(1<=r['recent_count']<=r['legacy_count']<=5000 and r['recent_count']<=window and r['recent_count']<=n-old+1,'sample population')
  need(0<r['recent_tail_ns'] and 0<r['legacy_tail_ns'] and 0<=r['margin_ns']<=20000000 and r['previous_budget_ns']>=0,'tail/margin values')
  # A subset's quantile can exceed the full-bank quantile. Do not compare tails
  # numerically: the native selector retains each exact order statistic.
  use=r['recent_count']>=128;need(r['recent_used']==use,'support fallback')
  desired=(r['recent_tail_ns']if use else r['legacy_tail_ns'])+r['margin_ns']
  blocked=r['previous_budget_ns']>desired and n==prior
  need(r['decrease_blocked']==blocked,'new-feedback decrease guard')
  slew=not blocked and use and r['previous_budget_ns']-desired>1000000
  applied=r['previous_budget_ns'] if blocked else r['previous_budget_ns']-1000000 if slew else desired
  need(r['slew_limited']==slew and r['applied_budget_ns']==applied,'budget/slew arithmetic')
  if rolling:
   need(all(type(r[k])is int and 0<=r[k]<2**63 for k in ROLLING_INTS),'typed snapshot fields')
   need(0<r['copy_begin_host_ns']<=r['copy_end_host_ns']<=r['worker_apply_host_ns'],'snapshot host clock order')
   need(r['consumed_snapshots']==i+r['empty_snapshots'] and r['batch']==r['consumed_snapshots']+r['coalesced_snapshots'],'snapshot update accounting')
   need(all(r[k]<=f[k] for k in ('consumed_snapshots','coalesced_snapshots','empty_snapshots')) and r['batch']<=f['last_consumed_batch'],'snapshot/footer prefix')
   if previous_snapshot:
    need(r['pacer_id']==previous_snapshot['pacer_id'],'one rolling owner per exclusive sidecar')
    need(r['copy_begin_host_ns']>=previous_snapshot['copy_end_host_ns'] and r['worker_apply_host_ns']>=previous_snapshot['worker_apply_host_ns'],'snapshot time regression')
    need(all(r[k]>=previous_snapshot[k] for k in ('consumed_snapshots','coalesced_snapshots','empty_snapshots')),'snapshot count regression')
   previous_snapshot=r
   copy_times.append(r['copy_end_host_ns']-r['copy_begin_host_ns']);apply_delays.append(r['worker_apply_host_ns']-r['copy_end_host_ns']);feedback_lags.append(r['boundary_frame']-n)
  prev=last.get(r['pacer_id'])
  if prev:
   need(r['batch']>prev['batch'] and r['epoch']>=prev['epoch'],'bank/epoch ordering')
   need(r['previous_budget_ns']==prev['applied_budget_ns'],'budget continuity')
   need(prior==(prev['newest_feedback_frame']if r['epoch']==prev['epoch']else -1),'feedback watermark continuity')
  else:need(prior==-1 and r['previous_budget_ns']==0,'initial budget/watermark')
  last[r['pacer_id']]=r;counts['recent'if use else'fallback']+=1;counts['slew']+=slew
 result=dict(schema=1,status='pass',updates=f['records'],window_frames=window,counts=counts,feedback_bank='rolling' if rolling else 'alternating',
  raw_feedback_quantiles_recomputed=False,clock_conversion_verified=False,latency_qualified=False,promotion_eligible=False,
  scope='Bound source-produced summary arithmetic and coverage only; no raw feedback population, estimator optimality, latency or photon qualification.')
 if rolling:
  result['snapshot_accounting']={k:f[k] for k in ROLLING_FOOTER}
  result['copy_duration_host_ns']={'min':min(copy_times),'max':max(copy_times)}
  result['copy_end_to_apply_host_ns']={'min':min(apply_delays),'max':max(apply_delays)}
  result['issued_frontier_minus_newest_feedback_frames']={'min':min(feedback_lags),'max':max(feedback_lags)}
  result['eye_pair_completeness_verified']=False
  result['all_generated_snapshots_consumed']=f['unconsumed_snapshots']==0 and f['coalesced_snapshots']==0
  result['feedback_loss_measured']=False
 return result
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('trace',type=Path);p.add_argument('--sha256',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 with a.trace.open('rb')as f:raw=f.read(8*1024*1024+1)
 need(len(raw)<=8*1024*1024 and hashlib.sha256(raw).hexdigest()==a.sha256,'bounded exact input')
 def pairs(items):
  out={}
  for k,v in items:need(k not in out,'duplicate key');out[k]=v
  return out
 result=evaluate([json.loads(x,object_pairs_hook=pairs)for x in raw.splitlines()]);result['input_sha256']=a.sha256
 with a.out.open('x')as f:json.dump(result,f,indent=2);f.write('\n')
if __name__=='__main__':main()
