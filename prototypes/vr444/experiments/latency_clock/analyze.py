#!/usr/bin/env python3
"""Read-only 0.13.1 local timing and sampled feedback joins. Never a promotion gate."""
import argparse
import hashlib
import json
import math
from pathlib import Path


STAGES = ('encode_ms', 'encode_to_send_ms', 'delivery_ms', 'decoder_queue_ms',
          'decode_ms', 'decoded_to_blit_ms', 'blit_to_predicted_display_ms')


def distribution(values):
    values = sorted(values)
    if not values:
        return {'count': 0}
    return dict(count=len(values), min=values[0], max=values[-1],
                **{f'p{p}': values[math.ceil(len(values)*p/100)-1] for p in (50,95,99)})


def offset_bracket(query_host_ns, response_xr_ns, receive_host_ns):
    """Single-sample causal offset bracket, assuming a constant additive clock.

    Nonnegative path/handling delays only; symmetry is NOT assumed. Clock drift,
    timestamp error and age require additional bounds before using this later.
    """
    if any(type(v) is not int or v <= 0 for v in (query_host_ns,response_xr_ns,receive_host_ns)) or receive_host_ns < query_host_ns:
        raise ValueError('positive integer ordered timestamps required')
    low, high = response_xr_ns-receive_host_ns, response_xr_ns-query_host_ns
    return dict(offset_min_ns=low,offset_max_ns=high,
                midpoint_offset_ns=(low+high)/2,half_rtt_ns=(high-low)/2)


def analyze(presented, snapshots, warmup_seconds=2):
    first={}; previous_time=previous_frame=-1
    for row in presented:
        ids=('left','right','left_warp','right_warp')
        times=('submit_xr_ns','predicted_display_xr_ns','left_receive_xr_ns','right_receive_xr_ns',
               'left_decode_ready_xr_ns','right_decode_ready_xr_ns','selection_begin_xr_ns',
               'selection_end_xr_ns','end_frame_return_xr_ns')
        if any(type(row.get(k)) is not int or row[k]<0 for k in ids) or any(type(row.get(k)) is not int or not 0<row[k]<2**63 for k in times):
            raise ValueError('invalid presentation identity/timestamp')
        if row['left']!=row['right'] or row['left_warp']!=row['right_warp']:
            raise ValueError('mismatched stereo identity')
        if row['submit_xr_ns']<=previous_time or row['left']<previous_frame:
            raise ValueError('presentation clock/frame regression')
        previous_time,previous_frame=row['submit_xr_ns'],row['left']
        if not row['selection_begin_xr_ns']<=row['selection_end_xr_ns']<=row['end_frame_return_xr_ns']<=row['submit_xr_ns']:
            raise ValueError('invalid selection/submission order')
        for side in ('left','right'):
            if not row[side+'_receive_xr_ns']<=row[side+'_decode_ready_xr_ns']<=row['selection_end_xr_ns']:
                raise ValueError('invalid eye receive/publication time')
        if row['left'] in first:
            stable=ids+('left_receive_xr_ns','right_receive_xr_ns','left_decode_ready_xr_ns','right_decode_ready_xr_ns')
            if any(row[k]!=first[row['left']][k] for k in stable):
                raise ValueError('repeated identity changed')
        else:
            first[row['left']]=row
    if not first:
        raise ValueError('no coherent presentations')
    cutoff=next(iter(first.values()))['submit_xr_ns']+int(warmup_seconds*1e9)
    warmed={frame:r for frame,r in first.items() if r['submit_xr_ns']>=cutoff}
    local={key:[] for key in ('predicted_target_minus_submit_ms','predicted_target_minus_end_frame_return_ms',
                             'predicted_target_minus_selection_end_ms','latest_eye_ready_to_selection_end_ms',
                             'earliest_eye_receive_to_submit_ms')}
    for r in warmed.values():
        for name,start,end in (
            ('predicted_target_minus_submit_ms',r['submit_xr_ns'],r['predicted_display_xr_ns']),
            ('predicted_target_minus_end_frame_return_ms',r['end_frame_return_xr_ns'],r['predicted_display_xr_ns']),
            ('predicted_target_minus_selection_end_ms',r['selection_end_xr_ns'],r['predicted_display_xr_ns']),
            ('latest_eye_ready_to_selection_end_ms',max(r['left_decode_ready_xr_ns'],r['right_decode_ready_xr_ns']),r['selection_end_xr_ns']),
            ('earliest_eye_receive_to_submit_ms',min(r['left_receive_xr_ns'],r['right_receive_xr_ns']),r['submit_xr_ns'])):
            local[name].append((end-start)/1e6)
    seen={};sessions=set();duplicates=0
    for outer in snapshots:
        s=outer['performance'];session=s['session']
        if type(session) is not int or session<=0:
            raise ValueError('invalid performance session')
        sessions.add(session)
        for stream in s['streams']:
            if stream['id'] not in (0,1) or stream.get('first_display') is None:
                continue
            f=stream['first_display'];frame=f.get('frame_index')
            if type(frame) is not int or frame<0:
                raise ValueError('invalid feedback frame')
            keys=STAGES+('encode_to_predicted_display_ms',)
            if any(type(f.get(k)) not in (int,float) or not math.isfinite(f[k]) or not 0<=f[k]<=2000 for k in keys):
                raise ValueError('invalid feedback timing')
            if abs(sum(f[k] for k in STAGES)-f['encode_to_predicted_display_ms'])>1e-6:
                raise ValueError('feedback stages do not telescope for one frame')
            key=(session,stream['id'],frame)
            if key in seen:
                if any(seen[key][k]!=f[k] for k in keys):
                    raise ValueError('feedback identity changed timing')
                duplicates+=1
            seen[key]=f
    if len(sessions)>1:
        raise ValueError('multiple performance sessions')
    joined=[];unmatched=repeat_first=0
    for (session,eye,frame),f in seen.items():
        r=warmed.get(frame)
        if r is None:
            unmatched+=1;continue
        # Feedback times_displayed==1 has to identify this same first successful
        # xrEndFrame. Do not guess after a failed/end-frame-unlogged first call.
        if r.get('left_repeat_count')!=1 or r.get('right_repeat_count')!=1:
            repeat_first+=1;continue
        lead=(r['predicted_display_xr_ns']-r['submit_xr_ns'])/1e6
        joined.append(dict(session=session,eye=eye,frame=frame,
            predicted_target_minus_submit_ms=lead,
            nominal_converted_encode_to_submit_ms=f['encode_to_predicted_display_ms']-lead,
            **{k:f[k] for k in STAGES+('encode_to_predicted_display_ms',)}))
    paired={frame for frame in warmed if {(r['eye']) for r in joined if r['frame']==frame}=={0,1}}
    paired_nominal=[max(r['nominal_converted_encode_to_submit_ms'] for r in joined if r['frame']==frame)
                    for frame in paired]
    return dict(status='analyzed',promotion_eligible=False,source_epoch_available=False,
                source_target_available=False,clock_error_bound_available=False,
                input_presentations=len(presented),first_unique_pairs=len(first),warm_unique_pairs=len(warmed),
                window_start_xr_ns=cutoff,local_ms={k:distribution(v) for k,v in local.items()},
                sampled_feedback=dict(unique_eye_first_displays=len(seen),duplicate_snapshots=duplicates,
                    excluded_unmatched_or_warmup=unmatched,excluded_first_success_was_repeat=repeat_first,
                    joined_eyes=len(joined),both_eyes_joined_pairs=len(paired),
                    paired_nominal_earliest_converted_encode_to_submit_ms=distribution(paired_nominal),
                    distributions_ms={k:distribution([r[k] for r in joined]) for k in
                        STAGES+('encode_to_predicted_display_ms','nominal_converted_encode_to_submit_ms')},
                    joined_rows=joined),
                scope='Local values are measured event/predicted-target differences. Converted host values are nominal, have unbounded measured synchronization error, and are not source age or photons. Feedback samples are about 200ms apart and not representative all-frame tail measurements.')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def analyze_run(directory):
    directory=Path(directory);manifest=json.loads((directory/'hashes.json').read_text())
    names=('client/codec-test-present.jsonl','performance-samples.jsonl','server.log','provenance.json')
    hashes={name:sha(directory/name) for name in names}
    if any(manifest.get(k)!=v for k,v in hashes.items()):
        raise ValueError('input does not match run manifest')
    get_rows=lambda name:[json.loads(l) for l in (directory/name).read_text().splitlines() if l.strip()]
    report=analyze(get_rows(names[0]),get_rows(names[1]))
    report.update(run=directory.name,input_sha256=hashes,
        clock_offset_trace_rows=sum('clock relations:' in l for l in (directory/'server.log').read_text().splitlines()))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs',nargs='+',type=Path);parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    report=dict(schema=1,analyzer_sha256=sha(__file__),runs=[analyze_run(d) for d in args.runs])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    for r in report['runs']:
        local=r['local_ms'];sample=r['sampled_feedback']
        print(r['run'], 'local predicted target-submit p50',local['predicted_target_minus_submit_ms'].get('p50'),
              'nominal converted encode-submit p50',sample['distributions_ms']['nominal_converted_encode_to_submit_ms'].get('p50'),
              'joined eyes',sample['joined_eyes'],'paired',sample['both_eyes_joined_pairs'])
