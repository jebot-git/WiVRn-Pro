#!/usr/bin/env python3
import argparse,hashlib,importlib.util,json,math,re
from pathlib import Path
if not __debug__:raise RuntimeError("audit requires enabled Python assertions")
p=argparse.ArgumentParser();p.add_argument('repo',type=Path);p.add_argument('campaign',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
repo=a.repo.resolve();campaign=a.campaign.resolve();bindings={}
def digest(raw):return hashlib.sha256(raw).hexdigest()
def raw(path):
 b=path.read_bytes();bindings[str(path)]={'sha256':digest(b),'bytes':len(b)};return b
def load(name,path):
 b=raw(path);s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);assert digest(path.read_bytes())==digest(b);return m
def dist(v):
 v=sorted(v);return None if not v else dict(count=len(v),p50=v[math.ceil(len(v)*.5)-1],p95=v[math.ceil(len(v)*.95)-1],p99=v[math.ceil(len(v)*.99)-1],max=v[-1])
def ns(v):return dist([x/1e6 for x in v])
metrics=load('abba_native_metrics',repo/'validation/wivrn-codecs/integration/native_metrics.py')
join=load('abba_pacer_join',repo/'prototypes/vr444/experiments/pacer_timing/join_source.py')
tail=load('abba_tail',repo/'validation/wivrn-codecs/integration/pacer_tail.py')
for q in (Path(join.pacer.__file__),Path(join.source.__file__),tail.ANALYZER):raw(q)
for name in tail.HELPERS:raw(tail.OVERLAY/name)
c=json.loads(raw(campaign/'campaign.json'))
assert c['status']=='complete' and not c.get('final_check_errors')
for k in ('final_config_matches_initial','own_reverse_absent','dedicated_app_stopped','production_package_unchanged','analysis_sources_unchanged'):assert c[k] is True,k
order=['rrb4-tail-0-1','rrb4-tail-900-2','rrb4-tail-900-3','rrb4-tail-0-4'];reports=[];builds=[]
for name,win in zip(order,[0,900,900,0]):
 root=campaign/name;manifest=json.loads(raw(root/'hashes.json'))
 def read(rel):
  b=raw(root/rel);assert digest(b)==manifest[rel],rel;return b
 def obj(rel):return json.loads(read(rel))
 def rows(rel):return [json.loads(line) for line in read(rel).splitlines()]
 s=obj('summary.json');prov=obj('provenance.json');cl=obj('cleanup.json');assert cl['restored'] is True and not cl['cleanup_errors']
 assert s['status']=='pass' and not s['comparison_gate_errors']
 assert s['metrics_source_sha256']==bindings[str(Path(metrics.__file__))]['sha256']
 for helper,sha in s['pacer_trace']['analyzer_sources_sha256'].items():assert bindings[str(repo/helper)]['sha256']==sha
 for k in ('candidate_trace','source_trace','pacer_trace','publication_trace'):assert s[k]['status']=='pass',(name,k)
 assert prov['pacer_window_frames']==win
 builds.append(tuple(prov[k] for k in ('server_sha256','installed_apk_sha256','runtime_manifest_sha256')))
 presented=rows('client/codec-test-present.jsonl');validated=metrics.evaluate_present(presented)
 assert validated['status']=='pass' and validated['after_warmup']==s['presentation']['after_warmup']
 cutoff=validated['after_warmup']['window_start_xr_ns'];seen=set();warm=[]
 for r in presented:
  if r['left'] not in seen and r['submit_xr_ns']>=cutoff:warm.append(r)
  seen.add(r['left'])
 assert len(warm)==s['research_latency_single_run']['unique_pairs']
 stages={k:[] for k in ('receive_to_submit','receive_to_both_ready','both_ready_to_selection','selection_to_end_frame','end_frame_to_recorded_submit','fence_wait','selection_work')}
 for r in warm:
  start=min(r['left_receive_xr_ns'],r['right_receive_xr_ns']);ready=max(r['left_decode_ready_xr_ns'],r['right_decode_ready_xr_ns']);sel=r['selection_begin_xr_ns'];end=r['end_frame_return_xr_ns'];submit=r['submit_xr_ns']
  assert start<=ready<=sel<=end<=submit
  vals=(submit-start,ready-start,sel-ready,end-sel,submit-end,r['render_fence_end_xr_ns']-r['render_fence_begin_xr_ns'],r['selection_end_xr_ns']-sel)
  for k,v in zip(stages,vals):stages[k].append(v)
 stereo={k:ns(v) for k,v in stages.items()}
 for q in ('p50','p95','p99'):assert stereo['receive_to_submit'][q]==s['research_latency_single_run']['observed']['stereo_receive_to_submit_ms_'+q]
 pacer=rows('host/pacer-trace.jsonl');source=rows('host/source-trace.jsonl');joined=join.evaluate(pacer,source,[r['left'] for r in warm]);assert joined['status']=='pass',joined.get('errors')
 frames={r['system_frame_id']:r for r in source[1:-1] if r['event']=='compositor_frame'}
 pred={}
 for r in pacer[1:-1]:pred.setdefault((r['frame_id'],r['display_ns']),[]).append(r)
 selected=[]
 for r in warm:
  f=frames[r['left']];matches=pred[(r['left'],f['display_time_ns'])];assert len(matches)==1;selected.append(matches[0])
 pid=int(read('app-pid.txt').strip());log=read('wivrn-live-logcat.txt').decode()
 focus=[]
 for line in log.splitlines():
  match=re.search(r'\S+\s+\S+\s+(\d+)\s+\d+.*Session state changed at timestamp (\d+): .* => XR_SESSION_STATE_(\w+)',line)
  if match and int(match[1])==pid:focus.append((int(match[2]),match[3]))
 focus.sort();assert focus
 latest=[state for when,state in focus if when<=warm[0]['submit_xr_ns']]
 focused=bool(latest and latest[-1]=='FOCUSED' and all(state=='FOCUSED' for when,state in focus if warm[0]['submit_xr_ns']<when<=warm[-1]['submit_xr_ns']))
 actual_tail=tail.evaluate(root,win);assert actual_tail['status']==('pass'if win else'off'),actual_tail
 tail_report={'status':actual_tail['status']}
 if win:
  trace=rows(tail.TRACE);assert actual_tail['existing_run_manifest_verified'] is True
  assert actual_tail['input_sha256']==s['pacer_tail']['input_sha256']
  updates=trace[1:-1];last=updates[-1];delta=[r['applied_budget_ns']-(r['legacy_tail_ns']+r['margin_ns']) for r in updates]
  tail_report.update(updates=len(updates),counts=actual_tail['counts'],existing_run_manifest_verified=True,first_update=updates[0],last_update=last,
   applied_minus_same_bank_legacy_budget_ms=ns(delta),minimum_applied_minus_same_bank_legacy_budget_ms=min(delta)/1e6,updates_below_same_bank_legacy=sum(x<0 for x in delta),updates_above_same_bank_legacy=sum(x>0 for x in delta),final_applied_minus_same_bank_legacy_budget_ms=delta[-1]/1e6,
   decreases_blocked=sum(r['decrease_blocked']for r in updates),epochs=sorted(set(r['epoch']for r in updates)),
   per_update=[{k:r[k]for k in ('boundary_frame','newest_feedback_frame','legacy_count','recent_count','legacy_tail_ns','recent_tail_ns','applied_budget_ns','recent_used','slew_limited','decrease_blocked')}for r in updates])
 period=sorted(set(r['predicted_display_period_xr_ns']for r in warm));mid=(warm[0]['submit_xr_ns']+warm[-1]['submit_xr_ns'])//2
 halves=[]
 for start,end in ((warm[0]['submit_xr_ns'],mid),(mid,warm[-1]['submit_xr_ns']+1)):
  part=[(r,p) for r,p in zip(warm,selected) if start<=r['submit_xr_ns']<end]
  halves.append(dict(count=len(part),receive_to_submit_ms=ns([r['submit_xr_ns']-min(r['left_receive_xr_ns'],r['right_receive_xr_ns']) for r,p in part]),safe_decode_budget_ms=ns([p['safe_decode_ns']for r,p in part])))
 reports.append(dict(name=name,window_frames=win,status='pass',unique_pairs=len(warm),hz=validated['after_warmup']['unique_stereo_submission_hz'],warm_seconds=validated['after_warmup']['unique_pair_interval_s'],stereo_stages_ms=stereo,
  focus=dict(pid=pid,observed_events=focus,warm_focused=focused),predicted_period_ns={'min':min(period),'max':max(period),'distinct':len(period)},selected_host_budget_and_phase_ms=joined['selected_host_budget_and_phase_ms'],
  first_warm_safe_decode_ms=selected[0]['safe_decode_ns']/1e6,last_warm_safe_decode_ms=selected[-1]['safe_decode_ns']/1e6,tail=tail_report,halves=halves,
  old_latency_gate_status=s['latency_gate']['status'],strict_sub15_status=s['research_latency_single_run']['status'],promotion_status=s['promotion_status'],quality_status=s['quality']))
assert len(set(builds))==1
out=dict(schema=1,status='pass',metric='First successful coherent stereo submission minus earliest eye receipt; first-use population after2s warmup; nearest-rank quantiles',same_recorded_build_all_arms=True,
 build_identity=dict(zip(('server_sha256','installed_apk_sha256','runtime_manifest_sha256'),builds[0])),runs=reports,campaign_postconditions={k:c[k] for k in ('status','final_config_matches_initial','own_reverse_absent','dedicated_app_stopped','production_package_unchanged','analysis_sources_unchanged','final_check_errors')},
 raw_feedback_quantiles_recomputed=False,causal_speedup_established=False,overhead_measured=False,latency_qualified=False,quality_qualified=False,promotion_eligible=False,
 scope='Recorded summary gates bound to final run manifests; presentation metrics and exact warm host source/pacer joins independently recomputed; enabled tail audit replayed with manifest. No host/client clock subtraction; no additive stage quantiles. Raw feedback samples and worker runtime costs are not recorded. Four runs are exploratory, not randomized or independent qualification.',input_artifacts=bindings)
out['audit_script_sha256']=digest(Path(__file__).read_bytes())
for path,record in bindings.items():assert digest(Path(path).read_bytes())==record['sha256'],path
out['all_read_inputs_unchanged_after_analysis']=True
with a.out.open('x')as stream:stream.write(json.dumps(out,indent=2)+'\n')
for r in reports:
 print(r['name'],'stereo',r['stereo_stages_ms']['receive_to_submit'],'Hz',r['hz'],'ready_residency',r['stereo_stages_ms']['both_ready_to_selection'],'safe',r['selected_host_budget_and_phase_ms']['safe_decode_ns'],'tail_finaldelta',r['tail'].get('final_applied_minus_same_bank_legacy_budget_ms'))
print('output',a.out)
