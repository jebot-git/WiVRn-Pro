"""Default-off source-map acquisition admission; formal comparison is post-seal."""
import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('source_map_control_audit',HERE/'source_mapping.py')
mapping=importlib.util.module_from_spec(s);s.loader.exec_module(mapping)
capture=mapping.capture
require=capture.require
FLAGS=('source_mapping_build_receipt','source_mapping_build_receipt_sha256',
       'source_mapping_app_build_receipt','source_mapping_app_build_receipt_sha256')

def read(path,limit):
    path=Path(path).absolute();require(not any(p.is_symlink() for p in (path,*path.parents)),'symlink source mapping input')
    require(path.is_file(),'missing source mapping input')
    with path.open('rb') as f:data=f.read(limit+1)
    require(len(data)<=limit,'source mapping input exceeds bound');return data

def validate(args,source=None):
    enabled=getattr(args,'source_mapping',False);require(type(enabled) is bool,'typed source mapping opt-in')
    if not enabled:
        require(all(getattr(args,k,None) is None for k in FLAGS),'source mapping receipts without opt-in');return ()
    for key,value in dict(mode='custom-rrb32',rrb_source='q1',q=1,compression='none',source_trace=True,quality_capture=True).items():
        require(type(getattr(args,key,None)) is type(value) and getattr(args,key)==value,'source mapping requires '+key)
    require(getattr(args,'quality_application_plan',None) is not None,'explicit quality application required')
    for pathkey,shakey in ((FLAGS[0],FLAGS[1]),(FLAGS[2],FLAGS[3])):
        require(getattr(args,pathkey,None) is not None and capture.valid_sha(getattr(args,shakey,None)),'both explicit source mapping receipt pins required')
        require(capture.digest(read(getattr(args,pathkey),16*1024*1024))==getattr(args,shakey),'source mapping receipt pin mismatch')
    build=capture.strict_json(read(getattr(args,FLAGS[0]),16*1024*1024));app_build=capture.strict_json(read(getattr(args,FLAGS[2]),4*1024*1024))
    for name in mapping.SOURCES:
        expected=read(HERE/'source-overlay'/name,8*1024*1024)
        require(build.get('sha256',{}).get(name)==capture.digest(expected),'source mapping native receipt source differs: '+name)
        if source is not None:require(read(Path(source)/name,8*1024*1024)==expected,'source mapping native source differs: '+name)
    require(set(build.get('foveation_shaders',{}))=={'foveation_rgb.comp'} and type(build.get('monado_source_view_contract')) is dict,'source mapping shader/external view receipt missing')
    plan_raw=read(args.quality_application_plan,65536)
    require(capture.digest(plan_raw)==getattr(args,'quality_application_plan_sha256',None),'source mapping application plan pin')
    plan=capture.strict_json(plan_raw)
    require(plan.get('schema')==2 and plan.get('source_reference_space')=='LOCAL','converted source mapping requires explicit LOCAL app plan')
    require(plan.get('executable_sha256')==app_build.get('binary_sha256',{}).get('quality-stimuli'),'source mapping app binary/build mismatch')
    for name in ('app.cpp','pattern.hpp'):
        require(app_build.get('source_sha256',{}).get(name)==capture.digest(read(mapping.ROOT/'prototypes/vr444/experiments/quality_stimuli'/name,1024*1024)),'source mapping app implementation differs')
    return mapping.SOURCES

def configure(args,env,output):
    """Always scrub inherited activation; mutate only the supplied env mapping."""
    env.pop('WIVRN_CODEC_SOURCE_MAPPING_TRACE',None)
    validate(args)
    if not getattr(args,'source_mapping',False):return {}
    path=Path(output).absolute()/mapping.TRACE
    require(not path.exists() and not path.is_symlink(),'source mapping trace must be new')
    env['WIVRN_CODEC_SOURCE_MAPPING_TRACE']=str(path)
    env['WIVRN_CODEC_TEST_QUALITY_CAPTURE']='1'
    env['WIVRN_CODEC_TEST_FRAME_STRIDE']='1'
    return dict(source_mapping=True,source_mapping_trace=mapping.TRACE,
        source_mapping_build_receipt_sha256=args.source_mapping_build_receipt_sha256,
        source_mapping_app_build_receipt_sha256=args.source_mapping_app_build_receipt_sha256,
        source_mapping_adapter_sha256=capture.digest(read(Path(__file__),1024*1024)),
        source_mapping_analyzer_sha256=capture.digest(read(HERE/'source_mapping.py',1024*1024)),
        source_mapping_scope='quality-only source mapping; provisional acquisition checks, formal post-seal audit required')

def evaluate(args,output):
    """Early acquisition checks, before summary/final manifest. Never full Q."""
    result=dict(schema=1,status='fail',provisional=True,final_run_manifest_verified=False,
        full_source_comparison_performed=False,source_stage_qualified=False,
        quality_pass=False,latency_eligible=False,full_goal_qualified=False,input_artifacts={})
    output=Path(output)
    def take(name,limit=128*1024*1024):
        data=read(output/name,limit);result['input_artifacts'][name]=dict(sha256=capture.digest(data),bytes=len(data));return data
    def jsonl(name,limit=128*1024*1024):return [capture.strict_json(line) for line in take(name,limit).splitlines()]
    try:
        validate(args)
        if not getattr(args,'source_mapping',False):
            require(not (output/mapping.TRACE).exists(),'source mapping trace while disabled');return result|dict(status='off')
        prov=capture.strict_json(take('provenance.json',4*1024*1024))
        require(prov.get('source_mapping') is True and prov.get('custom_q')==1 and prov.get('custom_compression')=='none' and prov.get('rrb_source')=='q1' and prov.get('custom_frame_stride')==1,'source map acquisition provenance mismatch')
        build=capture.strict_json(read(args.source_mapping_build_receipt,16*1024*1024))
        for key,name in capture.BINARIES.items():require(prov.get(key)==build.get('sha256',{}).get(name) and capture.valid_sha(prov.get(key)),'source map acquired binary/build identity')
        for name in mapping.SOURCES:require(prov.get('source_helper_sha256',{}).get(name)==build['sha256'][name],'source mapping acquired source identity')
        mapped=mapping.records(jsonl(mapping.TRACE,32*1024*1024));app_rows=jsonl('app-source/app.jsonl',32*1024*1024)
        # Clock/header validation is independently repeated here; full original
        # app byte/readback audit remains the caller's existing source gate.
        head=app_rows[0]
        require(head.get('schema')==3 and head.get('time_conversion_extension')=='XR_KHR_convert_timespec_time' and head.get('time_conversion_function')=='xrConvertTimeToTimespecTimeKHR' and head.get('converted_clock')=='CLOCK_MONOTONIC','actual runtime clock contract missing')
        for row in app_rows:
            if row.get('type')=='frame':
                ts=row.get('predicted_display_time_timespec');require(type(ts) is dict and set(ts)=={'tv_sec','tv_nsec'},'clock conversion components')
                sec=mapping.integer(ts['tv_sec'],0,(2**63-1)//10**9);ns=mapping.integer(ts['tv_nsec'],0,10**9-1)
                require(mapping.integer(row.get('predicted_display_time_monotonic_ns'),1,2**63-1)==sec*10**9+ns,'clock conversion target')
        joined,fresh=mapping.join(mapped,app_rows,jsonl('host/source-trace.jsonl'))
        encoded=set()
        for eye in (0,1):
            for row in jsonl(f'host/encode-{eye}.jsonl'):
                if row.get('type')=='rrb_pair_rejected':continue
                frame=mapping.integer(row.get('frame'),0,2**63-1);mapping.integer(row.get('eye'),eye,eye)
                if frame%90==0:
                    require((frame,eye) not in encoded,'duplicate sampled encode');encoded.add((frame,eye))
        require(encoded and encoded=={(f,e) for f,_ in encoded for e in (0,1)} and {f for f,e in encoded}<=set(joined),'sampled stereo encoded coverage missing')
        return result|dict(status='pass',mapped_frames=len(mapped),sampled_encoded_pairs=len(encoded)//2,
            exact_clock_and_source_joins=len(joined),source_trace_status=fresh['status'],
            formal_auditor='integration/source_mapping.py; explicit final run SHA plus native/app build receipt SHAs required')
    except (OSError,ValueError,TypeError,KeyError,OverflowError) as exc:return result|dict(errors=[str(exc)])
