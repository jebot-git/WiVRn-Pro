"""Pure literal source/submitted/replay RGB comparison; no file, GPU or device I/O.

Callers must first bind the full source_mapping/native capture/application audits.
This module rechecks the per-eye pixel/geometry contracts and never treats a
post-foveation image as the original reference. Native VBO bytes are not inferred.
"""
import copy
import importlib.util
from pathlib import Path
import struct
import numpy as np

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('original_pixels_mapping',HERE/'source_mapping.py')
mapping=importlib.util.module_from_spec(s);s.loader.exec_module(mapping)
capture=mapping.capture
require=capture.require


def f32(values):
    require(type(values) is list and all(capture.number(x) for x in values),'finite float32 vector')
    try:return struct.pack('<'+'f'*len(values),*values)
    except (struct.error,OverflowError) as exc:raise ValueError('float32 range') from exc


def _rgba(raw,w,h,fmt):
    require(type(raw) is bytes and len(raw)==w*h*4,'complete immutable raw pixels')
    require(type(fmt) is int and fmt in (43,50),'explicit native SRGB output format')
    return raw if fmt==43 else np.frombuffer(raw,np.uint8).reshape(h,w,4)[:,:,[2,1,0,3]].copy().tobytes()


def _alpha(a,b,mask):
    return int((a[:,:,3][mask]!=b[:,:,3][mask]).sum())


def prepare(record,eye,source_rgba,native_meta,app_view,*,encoded_rgba):
    """Return expected_rgba, immutable masks, and explicit per-eye proof.

    encoded_rgba is independently captured POST-host-foveation candidate data,
    used only to audit source-stage preservation over the required footprint.
    It never supplies a reference or alters the original application bytes.
    """
    require(capture.integer(eye,0,1),'typed eye')
    # Full caller audit validates cross-record order/lifetime. Repeat the complete
    # single record grammar without pretending its sequence starts a new run.
    single=copy.deepcopy(record);single['sequence']=1
    mapping.records([dict(mapping.HEADER),single,dict(type='footer',records=1,dropped=0,failed=0,complete=True)])
    meta=native_meta;e=record['eyes'][eye]
    output_mask=capture.validate_meta(meta,meta.get('capture_id'),eye)
    sw,sh=e['source_width'],e['source_height'];w,h=meta['width'],meta['height']
    require([w,h]==[sw,sh] and e['src_rect']==[0,0,sw,sh],'identity reference needs equal full positive source/output geometry')
    require(meta['encoded_input_extent']==[record['width'],record['height']],'encoded extent join')
    require(meta['foveation_x']==e['x_params'] and meta['foveation_y']==e['y_params'],'actual producer/consumer ratio join')
    require((meta['frame'],meta['warp'],meta['eye'])==(record['frame'],record['warp'],eye),'actual submitted/source identity')
    require(type(app_view) is dict and all(type(app_view.get(k)) is int for k in ('eye','width','height','image_index')) and app_view.get('eye')==eye and
            app_view.get('width')==sw and app_view.get('height')==sh and app_view.get('image_index')==e['image_index'] and app_view.get('readback_exact') is True,'actual source image/eye/shape/readback join')
    require(type(source_rgba) is bytes and len(source_rgba)==sw*sh*4 and capture.digest(source_rgba)==app_view.get('source_sha256'),'literal original application bytes')
    angles=mapping.app.vector(app_view.get('fov'),'radians',4)
    require(app_view['fov']['bits']==e['source_fov_bits_lrud'],'actual original FOV bit join')
    original_fov=struct.pack('<4I',*[e['source_fov_bits_lrud'][i]for i in(0,1,3,2)])
    for key in ('unwarped_fov','render_fov_left_right_down_up_radians','encoded_input_fov_left_right_down_up_radians'):
        require(len(meta[key])==4 and f32(meta[key])==original_fov,'exact original/submitted FOV')
    for key,count in (('position',3),('orientation_xyzw',4)):
        require(len(meta['render_pose'][key])==len(meta['encoded_input_pose'][key])==count and f32(meta['render_pose'][key])==f32(meta['encoded_input_pose'][key]),'no unaccounted pose reprojection')
    require(f32(meta['scale_rgba'])==f32([1,1,1,1]) and f32(meta['bias_rgba'])==f32([0,0,0,0]),'unity color transform required')
    require(meta['cutout_mask']==[[0,0,0,0]]*3 and meta['rect_a']==[0,0,0,0],'no cutout/alpha reference transform')
    require(meta['output_alpha'] is False and meta['sampled_rgb_is_linear'] is True and meta['shader_srgb_decode'] is False and meta['shader_alpha_specialization']==0 and not meta['alpha_image_bound'] and not meta['alpha_sampler_bound'],'opaque SRGB view pipeline required')
    src=np.frombuffer(source_rgba,np.uint8).reshape(sh,sw,4)
    require(bool(np.all(src[:,:,3]==255)),'opaque literal original source')
    expected=src[::-1,:,:].copy() if record['flip_y'] else src.copy()
    # A row permutation comes from actual UBO edges, never from a guessed
    # Vulkan/OpenXR convention. FOV asymmetry may make these masks unequal.
    px,py=capture.quality.perspective_edges(sw,sh,[angles[0],angles[1],angles[3],angles[2]])
    original_mask=capture.quality.protected_mask(sw,sh,px,py)
    transformed_source_mask=original_mask[::-1,:] if record['flip_y'] else original_mask
    mask=output_mask|transformed_source_mask
    require(bool(np.all(mask[output_mask])),'entire submitted protected cone must be retained')
    # Exact integer tilings derive both the output-region -> encoded-cell map
    # and the encoded-cell -> original-source footprint from captured UBOs.
    xe=np.asarray(e['x_edges'],dtype=np.int64);ye=np.asarray(e['y_edges'],dtype=np.int64)
    dx=np.diff(xe);dy=np.abs(np.diff(ye));xlo=xe[:-1];ylo=np.minimum(ye[:-1],ye[1:])
    iw,ih=record['width'],record['height'];xi=np.repeat(np.arange(iw),dx);yi=np.repeat(np.arange(ih),dy)
    require(len(xi)==w and len(yi)==h,'complete full-resolution inverse tiling')
    sy,sx=np.nonzero(mask);cells=np.zeros((ih,iw),bool);cells[yi[sy],xi[sx]]=True;halo=cells.copy()
    for oy in(-1,0,1):
        for ox in(-1,0,1):
            ya,yb=max(0,oy),min(ih,ih+oy);xa,xb=max(0,ox),min(iw,iw+ox)
            halo[ya:yb,xa:xb]|=cells[ya-oy:yb-oy,xa-ox:xb-ox]
    # The one-cell halo conservatively contains the adjacent LINEAR taps under
    # the source-bound native mesh. Actual output equality remains separately
    # measured; this never claims captured VBO or exact floating UV values.
    unit=(dy[:,None]==1)&(dx[None,:]==1);needed_unit=halo&unit
    require(type(encoded_rgba) is bytes and len(encoded_rgba)==iw*ih*4,'actual post-foveation candidate geometry')
    post=np.frombuffer(encoded_rgba,np.uint8).reshape(ih,iw,4)
    ey,ex=np.nonzero(needed_unit);original_cells=src[ylo[ey],xlo[ex]];post_cells=post[ey,ex]
    difference=np.abs(post_cells[:,:3].astype(np.int16)-original_cells[:,:3].astype(np.int16))
    mismatch=int(np.any(difference!=0,axis=1).sum());alpha=int((post_cells[:,3]!=original_cells[:,3]).sum())
    nonunit=int((halo&~unit).sum());opaque=bool(np.all(post[:,:,3]==255))
    full_required=halo[yi[:,None],xi[None,:]]
    source_required=full_required[::-1,:] if record['flip_y'] else full_required
    require(bool(np.all(source_required[original_mask])),'source cone excluded by footprint union')
    rgb=expected.tobytes();mask=np.ascontiguousarray(mask);mask.setflags(write=False)
    mask_u8=mask.astype(np.uint8).tobytes();source_u8=np.ascontiguousarray(source_required,dtype=np.uint8).tobytes()
    proof=dict(schema=1,eye=eye,frame=record['frame'],warp=record['warp'],capture_id=meta['capture_id'],width=w,height=h,vk_format=meta['vk_format'],
        original_sha256=capture.digest(source_rgba),expected_permuted_rgba_sha256=capture.digest(rgb),post_foveation_candidate_sha256=capture.digest(encoded_rgba),
        original_source_rectangle=e['src_rect'],row_flip=record['flip_y'],pixel_transform='proven full-rect row permutation only',resized=False,color_converted=False,
        original_source_fov_bits_lrud=e['source_fov_bits_lrud'],submitted_fov_bits_lrdu=list(struct.unpack('<4I',original_fov)),
        original_source_cone_pixels=int(original_mask.sum()),submitted_cone_pixels=int(output_mask.sum()),
        protected_union_pixels=int(mask.sum()),transformed_source_mask_equals_submitted=bool(np.array_equal(transformed_source_mask,output_mask)),
        mask_policy='union of entire actual submitted central10 and actual-UBO-permuted original-source central10; never intersection',
        mask_sha256=capture.digest(mask_u8),required_source_footprint_mask_sha256=capture.digest(source_u8),required_source_footprint_texels=int(source_required.sum()),
        required_encoded_cells=int(cells.sum()),required_encoded_halo_cells=int(halo.sum()),required_encoded_nonunit_halo_cells=nonunit,
        source_stage_unit_halo_rgb_mismatched_cells=mismatch,source_stage_unit_halo_max_code_error=int(difference.max())if difference.size else None,
        source_stage_unit_halo_alpha_mismatched_cells=alpha,source_stage_opaque_alpha=opaque,
        source_stage_required_footprints_exact=nonunit==0 and mismatch==alpha==0 and opaque,
        filter_support_scope='one encoded-cell halo under source-bound native LINEAR mesh; actual UV/VBO bytes not captured',
        original_pixels_used_as_reference=True,post_foveation_pixels_used_as_reference=False,
        actual_native_vertex_bytes_captured=False,actual_native_push_constant_bytes_captured=False,
        source_srgb_roundtrip_assumed_exact=False,full_goal_qualified=False,displayed_pixels_verified=False)
    return dict(expected_rgba=rgb,mask=mask,mask_u8=mask_u8,required_source_mask_u8=source_u8,proof=proof)


def compare(prepared,actual_native_raw,actual_replay_raw):
    """All comparisons are exact RGB; alpha and stage verdicts stay separate."""
    p=prepared['proof'];w,h,fmt=p['width'],p['height'],p['vk_format'];mask=prepared['mask'];expected=prepared['expected_rgba']
    require(type(expected) is bytes and capture.digest(expected)==p['expected_permuted_rgba_sha256'],'prepared original bytes changed')
    require(isinstance(mask,np.ndarray) and mask.dtype==np.bool_ and mask.shape==(h,w) and mask.any() and mask.astype(np.uint8).tobytes()==prepared['mask_u8'] and capture.digest(prepared['mask_u8'])==p['mask_sha256'],'prepared mask changed')
    native=_rgba(actual_native_raw,w,h,fmt);replay=_rgba(actual_replay_raw,w,h,fmt)
    reports={}
    for name,reference,candidate in [('native_to_original',expected,native),('replay_to_original',expected,replay),('native_to_replay',replay,native)]:
        row=capture.quality.compare_rgba(reference,candidate,w,h,mask)
        a=np.frombuffer(reference,np.uint8).reshape(h,w,4);b=np.frombuffer(candidate,np.uint8).reshape(h,w,4)
        row['protected_alpha_mismatched_pixels']=_alpha(a,b,mask);row['full_alpha_exact']=bool(np.all(a[:,:,3]==b[:,:,3]));reports[name]=row
    actual_codes=all(r['protected_exact'] and r['protected_alpha_mismatched_pixels']==0 and r['opaque_alpha_reference'] and r['opaque_alpha_candidate'] for r in reports.values())
    return dict(schema=1,scope='Finite actual submitted/replay vs literal original application RGB codes at protected union',
        proof=p,comparisons=reports,actual_submitted_and_replay_original_codes_exact=actual_codes,
        source_stage_required_footprints_exact=p['source_stage_required_footprints_exact'],
        finite_original_source_to_submitted_exact=actual_codes and p['source_stage_required_footprints_exact'],
        output_channel_permutation=[0,1,2,3]if fmt==43 else[2,1,0,3],
        raw_native_sha256=capture.digest(actual_native_raw),raw_replay_sha256=capture.digest(actual_replay_raw),
        no_rgb_tolerance=True,source_srgb_roundtrip_assumed_exact=False,
        full_goal_qualified=False,displayed_pixels_verified=False,latency_eligible=False,
        limitations=['Caller must independently bind complete stereo/frame/run/build/shader/physical-device provenance.',
                    'Both required post-foveation replay and original reference checks remain separate suite gates.',
                    'An exact finite pixel result is not actual native VBO capture, runtime timewarp or optical display evidence.'])
