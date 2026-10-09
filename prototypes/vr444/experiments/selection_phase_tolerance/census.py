#!/usr/bin/env python3
"""Historical observed-snapshot census only; no alternative live sequence replay."""
import argparse,hashlib,json,math
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('campaign',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();bindings={}
def digest(b):return hashlib.sha256(b).hexdigest()
def read(path,expected=None):
 b=path.read_bytes();h=digest(b)
 if expected is not None and h!=expected:raise ValueError('input hash mismatch: '+str(path))
 bindings[str(path)]={'sha256':h,'bytes':len(b)};return b
def dist(v):
 v=sorted(v);return {k:v[math.ceil(len(v)*q)-1]/1e6 for k,q in [('p05',.05),('p50',.5),('p95',.95)]}|{'min':v[0]/1e6,'max':v[-1]/1e6}
reports=[]
for name in ['rrb4-tail-0-1','rrb4-tail-900-2','rrb4-tail-900-3','rrb4-tail-0-4']:
 root=a.campaign/name;manifest=json.loads(read(root/'hashes.json'))
 def raw(n):return read(root/n,manifest[n])
 summary=json.loads(raw('summary.json'));cut=summary['presentation']['after_warmup']['window_start_xr_ns']
 if summary['status']!='pass' or summary['candidate_trace']['status']!='pass':raise ValueError('historical run did not pass')
 presents=[json.loads(x)for x in raw('client/codec-test-present.jsonl').splitlines()]
 traces=[json.loads(x)for x in raw('client/codec-test/selection-trace.jsonl').splitlines()]
 seen=set();warm={}
 for r in presents:
  if r['left']not in seen and r['submit_xr_ns']>=cut:warm[r['left']]=r
  seen.add(r['left'])
 counts={str(t):0 for t in (0,100000,250000,500000)};target=[];lead=[];residency=[];newer=0
 for r in traces:
  left,right=r['selected'][:2];fid=left['frame']
  if fid not in warm:continue
  t=r['predicted_display_xr_ns'];s=r['selection_begin_xr_ns'];period=r['predicted_display_period_xr_ns'];prior=r['previous_predicted_display_xr_ns']
  if warm[fid]['selection_begin_xr_ns']!=s or (left['frame'],left['warp'])!=(right['frame'],right['warp']):raise ValueError('selected join')
  target.append(left['view_display_time_xr_ns']-t);lead.append(t-s);residency.append(s-max(left['ready_xr_ns'],right['ready_xr_ns']))
  bound=min(period,t-prior)if period>0 and 0<prior<t else 0;alternatives=[]
  for x in r['history']:
   if not x['present']or x['decoder']!=0 or x['frame']<=fid or x['alpha']or x['view_display_time_xr_ns']<left['view_display_time_xr_ns']:continue
   if any(y['present']and y['decoder']==1 and all(y[k]==x[k]for k in ('frame','warp','view_display_time_xr_ns','alpha'))for y in r['history']):alternatives.append(x)
  newer+=bool(alternatives)
  for tol in counts:counts[tol]+=bool(bound and any(x['view_display_time_xr_ns']<=t+bound+int(tol)for x in alternatives))
 if len(residency)!=len(warm):raise ValueError('warm population mismatch')
 reports.append(dict(run=name,warm_pairs=len(warm),coherent_newer_pair_available=newer,future_tolerance_ns_admission_counts=counts,selected_target_minus_runtime_ms=dist(target),runtime_target_minus_selection_ms=dist(lead),ready_to_selection_ms=dist(residency)))
for path,d in bindings.items():
 if digest(Path(path).read_bytes())!=d['sha256']:raise ValueError('input changed')
result=dict(schema=1,status='historical_observation_only',runs=reports,input_artifacts=bindings,script_sha256=digest(Path(__file__).read_bytes()),historical_summary_gate_status_imported=True,full_trace_validators_rerun=False,counterfactual_sequence_replayed=False,alternative_cadence_measured=False,latency_qualified=False,quality_qualified=False,scope='Same recorded snapshots only. No native selector changes, future live timing prediction, pose fidelity or photon claim. Superseded by standalone codec scope; historical sub15 results are not sub5 qualification.')
with a.out.open('x')as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'status':result['status'],'runs':len(reports)}))
