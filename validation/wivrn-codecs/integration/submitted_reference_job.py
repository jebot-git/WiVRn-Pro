#!/usr/bin/env python3
"""Build a bounded RLP1 job from independent host input, not captured candidate pixels."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import zlib

HERE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location('submitted_reference_capture',HERE/'submitted_capture.py')
capture=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(capture)
require=capture.require


def envelope(data):
    require(type(data) is bytes and 48<len(data)<=48+16*1024*1024,'source envelope size')
    magic,kind,w,h,eye,size,frame,warp,crc,flags=struct.unpack('<6I2Q2I',data[:48])
    require(magic==0x31544357 and kind in (2,4) and eye in (0,1) and len(data)==48+size
            and size>0 and not flags&~1 and (not flags or kind==2),'source envelope framing/route')
    capture.quality.shape(w,h);require(w>=2 and h>=2,'native envelope minimum dimensions')
    require(zlib.crc32(data[48:])==crc,'source envelope payload CRC')
    return dict(kind=kind,width=w,height=h,eye=eye,frame=frame,warp=warp,flags=flags,payload_bytes=len(data))


def replay_eye(meta,raw):
    w,h=meta['encoded_input_extent']
    require(meta['rect_rgb']==[0,0,w,h],'RLP1 requires complete source rectangle')
    require(meta['shader_alpha_specialization']==0 and meta['shader_srgb_decode'] is False
            and meta['alpha_image_bound'] is False and meta['alpha_sampler_bound'] is False
            and meta['output_alpha'] is False,'RLP1 initial opaque/specialization scope')
    require(meta['sampled_rgb_is_linear'] is True and meta['input_view_vk_format']==43,'RLP1 SRGB input view scope')
    for axis in ['foveation_x','foveation_y']:
        require(len(meta[axis])<=129 and all(n<=4096 for n in meta[axis]),'RLP1 foveation bound')
    require(len(raw)==w*h*4 and all(raw[i]==255 for i in range(3,len(raw),4)),'independent source must be complete opaque RGBA')
    floats=meta['scale_rgba']+meta['bias_rgba']+[v for row in meta['cutout_mask'] for v in row]
    require(all(capture.number(v) for v in floats),'finite native push constants')
    # The metadata emits nine significant decimal digits for each float, enough
    # for exact float32 round-trip. Vertex arithmetic is reconstructed separately
    # by the source-bound native replay and is not actual captured VBO bytes.
    try:pc=struct.pack('<8i20f',*meta['rect_rgb'],*meta['rect_a'],*floats)
    except (struct.error,OverflowError) as exc:raise ValueError('RLP1 push-constant range') from exc
    x,y=meta['foveation_x'],meta['foveation_y']
    return (struct.pack('<6I',w,h,len(x),len(y),0,0)+pc
            +struct.pack('<'+'I'*(len(x)+len(y)),*(x+y))+raw)


def make_job(run_root,build_receipt,build_receipt_sha256,capture_id,build_root=None,run_hashes_sha256=None):
    require(capture.integer(capture_id,1,8),'typed bounded capture ID')
    context={}
    report,unused_products=capture.audit(run_root,build_receipt,build_receipt_sha256,build_root,run_hashes_sha256,_context=context)
    del unused_products
    require(capture_id<=report['captured_pairs'],'capture ID outside audited population')
    run,build=context['run'],context['build']
    for name in ['server/encoder/video_encoder_test.cpp','common/codec_test/wire.h']:
        build.read(name,8*1024*1024)
    stem=f'capture-{capture_id}'
    metas=[run.json(capture.PREFIX+stem+f'-eye-{eye}.json',1024*1024) for eye in (0,1)]
    output={'width':metas[0]['width'],'height':metas[0]['height'],'vk_format':metas[0]['vk_format']}
    job=bytearray(struct.pack('<6I',0x31504c52,1,output['width'],output['height'],output['vk_format'],2))
    sources=[]
    for eye,meta in enumerate(metas):
        frame,warp=meta['frame'],meta['warp']
        rows=run.jsonl(f'host/encode-{eye}.jsonl')
        exact=[];identities=set()
        for row in rows:
            require(type(row) is dict and all(capture.integer(row.get(k)) for k in ('frame','eye','warp')),'host encode typed identity')
            require(row['eye']==eye,'host encode file eye mismatch')
            key=(row['frame'],row['eye']);require(key not in identities,'duplicate host encode identity');identities.add(key)
            if key==(frame,eye):exact.append(row)
        require(len(exact)==1 and exact[0]['warp']==warp,'independent source has no exact host encode join')
        encoded=exact[0]
        raw_name=f'host/{frame}-{eye}.rgba';wire_name=f'host/{frame}-{eye}.envelope'
        raw=run.read(raw_name,capture.MAX_RAW);wire=run.read(wire_name,48+16*1024*1024);header=envelope(wire)
        expected={'frame':frame,'warp':warp,'eye':eye,'width':meta['encoded_input_extent'][0],'height':meta['encoded_input_extent'][1]}
        require(all(header[k]==v for k,v in expected.items()),'host source envelope/capture identity or dimensions differ')
        require(type(encoded.get('payload_bytes')) is int and encoded['payload_bytes']==len(wire),'host encode payload-size join')
        require(encoded.get('effective_codec')==('rrb32' if header['kind']==4 else 'vr444'),'host codec/envelope kind join')
        require(capture.integer(encoded.get('quantizer'),1,255),'host quantizer type/range')
        require(encoded.get('source_contract') in ('legacy','post_foveation_rgb_exact','decoded_vr444_rgb_exact'),'host source contract missing')
        job.extend(replay_eye(meta,raw))
        sources.append(dict(frame=frame,warp=warp,eye=eye,geometry=meta['encoded_input_extent'],
            source_boundary='post-host-foveation-codec-input',source={'path':raw_name,'sha256':capture.digest(raw),'bytes':len(raw)},
            envelope={'path':wire_name,'sha256':capture.digest(wire),'bytes':len(wire)},
            encode_event_input={'path':f'host/encode-{eye}.jsonl','sha256':run.hashes[f'host/encode-{eye}.jsonl']},
            codec=encoded['effective_codec'],quantizer=encoded['quantizer'],declared_source_contract=encoded['source_contract'],
            encoded_payload_pixels_used_as_reference=False,decoder_output_used_as_reference=False,
            original_application_rgb_claim=False))
    require(len(job)<=40*1024*1024,'bounded RLP1 job size')
    job=bytes(job);job_name=f'{stem}-independent-host-input.rlp'
    result={'schema':1,'status':'pass','capture_id':capture_id,'frame':metas[0]['frame'],'warp':metas[0]['warp'],
        'job':{'path':job_name,'sha256':capture.digest(job),'bytes':len(job),'format':'RLP1','version':1},'output':output,
        'independent_inputs':sources,'candidate_capture_audit':report,'renderer_shaders':report['renderer_shaders'],
        'run_hashes_sha256':report['run_hashes_sha256'],'build_receipt_sha256':build_receipt_sha256,
        'run_inputs':run.reads,'build_inputs':build.reads,
        'adapter_sources':{p.name:capture.digest(p.read_bytes()) for p in [Path(__file__),HERE/'submitted_capture.py',HERE/'research_quality.py']},
        'reference_pixels_rendered':False,'source_boundary':'post-host-foveation-codec-input',
        'geometry_reconstructed_from_metadata':True,'actual_native_vertex_bytes_captured':False,
        'actual_native_push_constant_bytes_captured':False,'aarch64_raster_geometry_bit_equivalence_verified':False,
        'quality_pass':False,'submitted_pixel_equality_verified':False,'displayed_pixels_verified':False,
        'latency_eligible':False,'full_goal_qualified':False,'promotion_eligible':False,
        'scope':'Independent host pre-codec source mapped to a private native-shader replay job; no GPU execution, reference render or final quality verdict.'}
    return result,{job_name:job}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--build-receipt',type=Path,required=True);p.add_argument('--build-receipt-sha256',required=True)
    p.add_argument('--capture-id',type=int,required=True);p.add_argument('--build-root',type=Path)
    p.add_argument('--run-hashes-sha256');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    report,products=make_job(a.run_root,a.build_receipt,a.build_receipt_sha256,a.capture_id,a.build_root,a.run_hashes_sha256)
    capture.write_output(a.out,report,products)
    print(json.dumps({'status':'pass','job':report['job'],'quality_pass':False}))

if __name__=='__main__':main()
