#!/usr/bin/env python3
"""Host-local callback timeline for exact selected source generations."""
import argparse
from collections import defaultdict
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
spec=importlib.util.spec_from_file_location('host_timeline_counterfactual',HERE.parent/'selection_policy/counterfactual.py')
cf=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)
STAGES=('wake_to_begin_ns','begin_to_delivered_ns','delivered_to_gpu_done_ns','gpu_done_to_latch_ns','latch_to_accepted_input_ns')
PRIMARY_REFERENCES=(
    'https://monado.pages.freedesktop.org/monado/structu__pacing__app.html',
    'https://monado.pages.freedesktop.org/monado/u__pacing_8h_source.html')


def exactly_one(values,description):
    if len(values)!=1:
        raise ValueError(description+' requires exactly one event')
    return values[0]


def summarize(samples):
    if not samples:
        return dict(count=0,stage_ms={},phase_ms={})
    stages={key:cf.distribution([row['intervals_ns'][key]/1e6 for row in samples]) for key in STAGES}
    stages['wake_to_accepted_input_ns']=cf.distribution([row['wake_to_accepted_input_ns']/1e6 for row in samples])
    phases={key:cf.distribution([row['phase_ns'][key]/1e6 for row in samples]) for key in samples[0]['phase_ns']}
    ordered=sorted(samples,key=lambda row:row['wake_to_accepted_input_ns'])
    return dict(count=len(samples),stage_ms=stages,phase_ms=phases,
        p95_total_ranked_sample=ordered[(len(ordered)*95+99)//100-1],
        five_largest_total_samples=list(reversed(ordered[-5:])))


def evaluate(traces,presented,source_rows,warmup_ns=2_000_000_000):
    if type(warmup_ns) is not int or warmup_ns<0:
        raise ValueError('nonnegative exact warm-up required')
    verified=cf.trace.evaluate(traces,presented)
    if verified['status']!='pass' or len(traces)!=len(presented) or not presented:
        raise ValueError('complete valid selected stereo trace required')
    needed={p['left'] for p in presented}
    source=cf.source.evaluate(source_rows,needed)
    if source['status']!='pass' or source.get('ambiguous_projection_matches'):
        raise ValueError('complete unambiguous host source joins required')
    generations={r['system_frame_id']:tuple((a['pacer_id'],a['app_frame_id']) for a in r['app_frames'])
        for r in source['system_to_app_frames']}
    apps=defaultdict(lambda:defaultdict(list));latches=defaultdict(list);app_latches=defaultdict(list);frames={}
    for row in source_rows[1:-1]:
        event=row['event'];key=(row['pacer_id'],row['app_frame_id'])
        if event=='compositor_frame':frames[row['system_frame_id']]=row
        elif event=='app_latched':
            latches[row['system_frame_id']].append(row);app_latches[key].append(row)
        elif event.startswith('app_'):apps[key][event].append(row)
    cutoff=presented[0]['submit_xr_ns']+warmup_ns
    seen_frames,seen_generations=set(),set()
    samples=[];pre_warm_frames=0;pre_warm_generations=set()
    for sequence,present in enumerate(presented,1):
        frame_id=present['left'];generation=generations[frame_id]
        first_frame=frame_id not in seen_frames;first_generation=generation not in seen_generations
        seen_frames.add(frame_id);seen_generations.add(generation)
        if present['submit_xr_ns']<cutoff:
            pre_warm_frames+=first_frame;pre_warm_generations.add(generation)
            continue
        if not first_frame:
            continue
        frame=frames[frame_id]
        if not frame['direct_projection'] or frame['projection_count']!=1 or len(generation)!=1:
            raise ValueError('host timeline currently requires a single direct projection source')
        key=generation[0];app=apps[key]
        latch=exactly_one(latches[frame_id],'selected compositor latch')
        if (latch['pacer_id'],latch['app_frame_id'])!=key:
            raise ValueError('source set and actual latch identity differ')
        prior_latches=sum((r['when_ns'],r['sequence'])<(latch['when_ns'],latch['sequence']) for r in app_latches[key])
        predict=exactly_one(app['app_predict'],'application prediction')
        delivered=exactly_one(app['app_delivered'],'application delivery')
        gpu=exactly_one(app['app_gpu_done'],'application GPU completion')
        # u_timing_point declaration: WAKE_UP=0, BEGIN=1. Keep the numeric ABI
        # and its primary header reference explicit rather than infer by order.
        wake=exactly_one([r for r in app['app_point'] if r['timing_point']==0],'application wake point')
        begin=exactly_one([r for r in app['app_point'] if r['timing_point']==1],'application begin point')
        clock=[wake['when_ns'],begin['when_ns'],delivered['when_ns'],gpu['when_ns'],latch['when_ns'],frame['when_ns']]
        if predict['when_ns']>clock[0] or any(a>b for a,b in zip(clock,clock[1:])):
            raise ValueError('selected application callback chronology regresses')
        intervals=dict(zip(STAGES,(b-a for a,b in zip(clock,clock[1:]))))
        total=clock[-1]-clock[0]
        if sum(intervals.values())!=total:
            raise ValueError('host-local interval sum differs')
        samples.append(dict(selection_sequence=sequence,encoded_frame=frame_id,pacer_id=key[0],app_frame_id=key[1],
            first_source_generation_in_actual_history=first_generation,prior_host_latches_of_source=prior_latches,
            intervals_ns=intervals,wake_to_accepted_input_ns=total,
            phase_ns=dict(actual_wake_minus_requested_wake_ns=clock[0]-predict['wake_up_ns'],
                compositor_prediction_minus_accepted_input_ns=frame['display_time_ns']-clock[-1],
                source_layer_target_minus_accepted_input_ns=frame['layer_target_ns']-clock[-1],
                source_layer_target_minus_compositor_prediction_ns=frame['layer_target_ns']-frame['display_time_ns'],
                delivered_target_minus_application_prediction_ns=delivered['display_time_ns']-predict['display_time_ns']),
            callback_observation_lag_ns={name:row['observed_ns']-row['when_ns'] for name,row in
                (('predict',predict),('wake',wake),('begin',begin),('delivered',delivered),('gpu_done',gpu),('latch',latch),('accepted_input',frame))}))
    if not samples:
        raise ValueError('no warm first encoded submissions')
    fresh=[s for s in samples if s['first_source_generation_in_actual_history']]
    repeated=[s for s in samples if not s['first_source_generation_in_actual_history']]
    return dict(schema=1,status='pass',analysis_kind='selected_host_local_callback_timeline',
        warmup_ns=warmup_ns,pre_warm_distinct_encoded_frames=pre_warm_frames,
        pre_warm_distinct_source_sets=len(pre_warm_generations),warm_first_encoded=summarize(samples),
        warm_first_source_generation=summarize(fresh),warm_reused_source_generation=summarize(repeated),
        warm_first_host_latch=summarize([s for s in samples if s['prior_host_latches_of_source']==0]),
        warm_repeated_host_latch=summarize([s for s in samples if s['prior_host_latches_of_source']>0]),
        observed_trace_validation=verified,source_validation={k:v for k,v in source.items() if k!='system_to_app_frames'},
        samples=samples,all_sample_stage_sums_exact=True,primary_references=list(PRIMARY_REFERENCES),
        measured_speedup=False,cross_host_client_subtraction=False,latency_gate_evaluated=False,
        native_behavior_changed=False,promotion_eligible=False,release_ready=False,
        assumptions=[
            'Only actual first encoded stereo submissions after warm-up; first source-generation status includes the actual pre-warm selection history, not unselected host frames.',
            'Prior host latches count all recorded latches of that exact application identity before this latch by callback time, with recorded sequence breaking equal-time ties; this separate population includes unselected host frames.',
            'Single direct-projection, one-latched-application cases only; other layouts fail closed instead of selecting one convenient layer.',
            'Host callback timestamps are one host monotonic domain. Client submit time is used only for warm membership, never subtracted from host time.',
            'The five per-sample intervals sum exactly; separately summarized stage percentiles must not be added.',
            'Application delivery does not mean GPU completion; delivered-to-GPU-done includes all elapsed callback-domain time, not isolated shader execution.',
            'Prediction and source-layer target lead are signed phase descriptions, not observed waiting, photons, or removable scheduling budgets.',
            'Application frame IDs do not establish distinct simulation/input epochs or changed pixels.',
            'Original source instrumentation adds overhead. Source completion footer qualifies recorded events, not an optical endpoint.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run,out=args.run.resolve(),args.out.resolve()
    if out.is_relative_to(run):parser.error('output must be outside immutable acquisition')
    sealed=cf.frozen.analyze(run)
    if sealed['status']!='pass':raise ValueError('sealed diagnostic evidence invalid')
    manifest_path=run/'hashes.json';manifest=json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']:raise ValueError('original manifest changed')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl','provenance.json')
    if not set(names).issubset(manifest):raise ValueError('host timeline inputs not manifest-bound')
    provenance=json.loads((run/names[-1]).read_text())
    if provenance.get('source_trace') is not True:raise ValueError('source trace not explicitly enabled in acquisition')
    helpers=('server/driver/app_pacer.cpp','server/driver/codec_source_trace.h','server/compositor/compositor.cpp')
    helper_paths=[ROOT/'validation/wivrn-codecs/integration/source-overlay'/name for name in helpers]
    for name,path in zip(helpers,helper_paths):
        if cf.frozen.sha(path)!=provenance.get('source_helper_sha256',{}).get(name):
            raise ValueError('current helper does not match acquisition source semantics: '+name)
    paths=[Path(__file__),Path(cf.__file__),Path(cf.trace.__file__),Path(cf.replay.__file__),Path(cf.source.__file__),Path(cf.frozen.__file__),*helper_paths]
    hashes={str(path.relative_to(ROOT)):cf.frozen.sha(path) for path in paths}
    rows=[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names[:3]]
    result=evaluate(*rows)
    if (any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']
            or any(cf.frozen.sha(ROOT/name)!=digest for name,digest in hashes.items())):
        raise ValueError('host timeline acquisition or source changed')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],original_result_overwritten=False,
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=sealed['original_manifest_files_verified'],sources_sha256=hashes)
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({name:result[name]['count'] for name in ('warm_first_encoded','warm_first_source_generation','warm_reused_source_generation')}))


if __name__=='__main__':main()
