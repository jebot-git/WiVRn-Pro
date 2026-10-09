import copy
import importlib.util
from pathlib import Path
import struct
import random
import unittest
import numpy as np

HERE=Path(__file__).resolve().parents[1]/'integration'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
p=load('pixeltests',HERE/'original_source_pixels.py')
source=load('mapfixtures',Path(__file__).with_name('test_source_mapping.py'))
capture=load('capturefixtures',Path(__file__).with_name('test_submitted_capture.py'))

def fixture(flip=False,asymmetric=False,fmt=43):
    r=source.mapping(flip=flip);meta=capture.CaptureAudit.meta(1,0,90,7,1000)
    angles=[-.5,.5,.7,-.4]if asymmetric else[-.5,.5,.5,-.5]
    for e in r['eyes']:e['source_fov_bits_lrud']=source.bits(angles)
    w,h=17,33;raw=p.mapping.app.expected('rgb-ramp',w,h,0)
    view=source.view(r,0);view['fov']=dict(radians=angles,bits=source.bits(angles));view.update(readback_exact=True,source_sha256=p.capture.digest(raw))
    fov=[angles[0],angles[1],angles[3],angles[2]]
    meta.update(width=w,height=h,row_bytes=w*4,bytes=w*h*4,subimage_rect=[0,0,w,h],rect_rgb=[0,0,w,h],encoded_input_extent=[w,h],
        foveation_x=[w],foveation_y=[h],unwarped_fov=fov,render_fov_left_right_down_up_radians=fov,encoded_input_fov_left_right_down_up_radians=fov,
        vk_format=fmt,channel_order='RGBA'if fmt==43 else'BGRA',canonical_rgba=fmt==43)
    encoded=np.frombuffer(raw,np.uint8).reshape(h,w,4)[::-1].copy().tobytes()if flip else raw
    return r,raw,meta,view,encoded

def prepare(values):
    r,raw,meta,view,encoded=values;return p.prepare(r,0,raw,meta,view,encoded_rgba=encoded)

def format_raw(rgba,fmt):return rgba if fmt==43 else np.frombuffer(rgba,np.uint8).reshape(-1,4)[:,[2,1,0,3]].copy().tobytes()

class OriginalSource(unittest.TestCase):
    def test_128_tilings_against_scalar_footprint_reference(self):
        rng=random.Random(4182)
        def axis():
            n=rng.choice((1,3,5));params=[rng.randrange(2,9)for _ in range(n)]
            widths=[abs(i-n//2)+1 for i,pixels in enumerate(params)for _ in range(pixels)]
            edges=[0]
            for span in widths:edges.append(edges[-1]+span)
            return params,edges
        for index in range(128):
            flip=bool(index%2);r,_,meta,view,_=fixture(flip,True)
            xp,xe=axis();yp,ye=axis();w,h=xe[-1],ye[-1];iw,ih=len(xe)-1,len(ye)-1
            if flip:ye=[h-v for v in ye]
            r.update(width=iw,height=ih)
            for e in r['eyes']:e.update(source_width=w,source_height=h,src_rect=[0,0,w,h],x_params=xp,y_params=yp,x_edges=xe,y_edges=ye)
            raw=p.mapping.app.expected('rgb-ramp',w,h,0);src=np.frombuffer(raw,np.uint8).reshape(h,w,4)
            view.update(width=w,height=h,source_sha256=p.capture.digest(raw))
            meta.update(width=w,height=h,row_bytes=w*4,bytes=w*h*4,subimage_rect=[0,0,w,h],rect_rgb=[0,0,iw,ih],
                encoded_input_extent=[iw,ih],foveation_x=xp,foveation_y=yp)
            encoded=b''.join(src[min(ye[y:y+2]),xe[x]].tobytes()for y in range(ih)for x in range(iw))
            prepared=p.prepare(r,0,raw,meta,view,encoded_rgba=encoded);mask=prepared['mask']
            # Independent scalar rectangle intersections and set-neighbor union.
            selected=set()
            for y in range(ih):
                a,b=sorted(ye[y:y+2]);a,b=(h-b,h-a)if flip else(a,b)
                for x in range(iw):
                    if mask[a:b,xe[x]:xe[x+1]].any():selected.add((y,x))
            halo={(y+dy,x+dx)for y,x in selected for dy in(-1,0,1)for dx in(-1,0,1)
                  if 0<=y+dy<ih and 0<=x+dx<iw}
            required=np.zeros((h,w),bool);nonunit=0
            for y,x in halo:
                a,b=sorted(ye[y:y+2]);required[a:b,xe[x]:xe[x+1]]=True
                nonunit+=int(b-a!=1 or xe[x+1]-xe[x]!=1)
            proof=prepared['proof']
            self.assertEqual(proof['required_encoded_cells'],len(selected))
            self.assertEqual(proof['required_encoded_halo_cells'],len(halo))
            self.assertEqual(proof['required_encoded_nonunit_halo_cells'],nonunit)
            self.assertEqual(prepared['required_source_mask_u8'],required.astype(np.uint8).tobytes())
            self.assertEqual(proof['source_stage_unit_halo_rgb_mismatched_cells'],0)
    def test_exact_both_formats_and_asymmetric_flip_union(self):
        for flip in(False,True):
            for asymmetric in(False,True):
                for fmt in(43,50):
                    values=fixture(flip,asymmetric,fmt);prepared=prepare(values);raw=format_raw(prepared['expected_rgba'],fmt)
                    result=p.compare(prepared,raw,raw)
                    self.assertTrue(result['finite_original_source_to_submitted_exact'])
                    self.assertFalse(result['full_goal_qualified']);self.assertFalse(result['displayed_pixels_verified'])
                    self.assertEqual(prepared['proof']['source_stage_unit_halo_rgb_mismatched_cells'],0)
                    if flip and asymmetric:
                        self.assertFalse(prepared['proof']['transformed_source_mask_equals_submitted'])
                        self.assertGreater(prepared['proof']['protected_union_pixels'],prepared['proof']['submitted_cone_pixels'])
    def test_shared_wrong_replay_and_native_cannot_pass(self):
        prepared=prepare(fixture());bad=bytearray(prepared['expected_rgba']);y,x=np.argwhere(prepared['mask'])[0];bad[(y*17+x)*4]^=1
        result=p.compare(prepared,bytes(bad),bytes(bad))
        self.assertTrue(result['comparisons']['native_to_replay']['protected_exact'])
        self.assertFalse(result['comparisons']['native_to_original']['protected_exact'])
        self.assertFalse(result['comparisons']['replay_to_original']['protected_exact'])
        self.assertFalse(result['finite_original_source_to_submitted_exact'])
        self.assertEqual(result['comparisons']['native_to_original']['protected_max_absolute_code_error'],1)
    def test_either_output_and_alpha_separate(self):
        prepared=prepare(fixture());original=prepared['expected_rgba'];y,x=np.argwhere(prepared['mask'])[0]
        for channel in(0,3):
            for which in(0,1):
                bad=bytearray(original);bad[(y*17+x)*4+channel]^=1;outputs=[original,original];outputs[which]=bytes(bad)
                result=p.compare(prepared,*outputs);self.assertFalse(result['finite_original_source_to_submitted_exact'])
                row=result['comparisons']['native_to_original'if which==0 else'replay_to_original']
                self.assertEqual(row['protected_alpha_mismatched_pixels'],int(channel==3))
                self.assertEqual(row['protected_changed_pixels'],int(channel==0))
    def test_source_halo_error_blocks_finite_success(self):
        values=list(fixture());good=prepare(values)
        halo=np.frombuffer(good['required_source_mask_u8'],np.uint8).reshape(33,17).astype(bool)
        outside=halo&~good['mask'];self.assertTrue(outside.any());y,x=np.argwhere(outside)[0]
        bad=bytearray(values[-1]);bad[(y*17+x)*4]^=1;values[-1]=bytes(bad);prepared=prepare(values)
        self.assertFalse(prepared['proof']['source_stage_required_footprints_exact'])
        self.assertEqual(prepared['proof']['source_stage_unit_halo_rgb_mismatched_cells'],1)
        result=p.compare(prepared,prepared['expected_rgba'],prepared['expected_rgba'])
        self.assertTrue(result['actual_submitted_and_replay_original_codes_exact']);self.assertFalse(result['finite_original_source_to_submitted_exact'])
    def test_nonunit_footprints_remain_usable_negative_reference(self):
        r,raw,meta,view,encoded=fixture()
        for e in r['eyes']:
            e.update(source_width=33,src_rect=[0,0,33,33],x_params=[8,1,8],x_edges=list(range(0,17,2))+[17]+list(range(19,34,2)))
        raw=p.mapping.app.expected('rgb-ramp',33,33,0);view.update(width=33,source_sha256=p.capture.digest(raw))
        meta.update(width=33,row_bytes=132,bytes=33*33*4,subimage_rect=[0,0,33,33],foveation_x=[8,1,8])
        prepared=p.prepare(r,0,raw,meta,view,encoded_rgba=encoded)
        self.assertGreater(prepared['proof']['required_encoded_nonunit_halo_cells'],0)
        self.assertFalse(p.compare(prepared,raw,raw)['finite_original_source_to_submitted_exact'])
    def test_identity_geometry_and_color_mutations(self):
        changes=[lambda r,raw,m,v,e:m.update(frame=180),lambda r,raw,m,v,e:m.update(warp=8),
            lambda r,raw,m,v,e:m['render_pose']['position'].__setitem__(0,1),
            lambda r,raw,m,v,e:m.update(scale_rgba=[.9,1,1,1]),lambda r,raw,m,v,e:m.update(bias_rgba=[.001,0,0,0]),
            lambda r,raw,m,v,e:m['cutout_mask'][0].__setitem__(3,1),lambda r,raw,m,v,e:v.update(image_index=1),
            lambda r,raw,m,v,e:v.update(image_index=False),lambda r,raw,m,v,e:v.update(width=17.0),
            lambda r,raw,m,v,e:v.update(height=33.0),lambda r,raw,m,v,e:v.update(eye=False),
            lambda r,raw,m,v,e:v.update(source_sha256='a'*64),lambda r,raw,m,v,e:v.update(readback_exact=False),
            lambda r,raw,m,v,e:m['encoded_input_fov_left_right_down_up_radians'].__setitem__(0,-.6),
            lambda r,raw,m,v,e:r['eyes'][0]['x_edges'].__setitem__(1,2),lambda r,raw,m,v,e:m['sampler_contract'].update(mag='NEAREST')]
        for change in changes:
            values=fixture();# break fixture's intentional sharing before splicing one actual field
            values=tuple(copy.deepcopy(x)for x in values)
            values[2]['render_pose']=copy.deepcopy(values[2]['render_pose']);values[2]['encoded_input_pose']=copy.deepcopy(values[2]['encoded_input_pose'])
            change(*values)
            with self.assertRaises(ValueError):prepare(values)
    def test_missing_pixels_changed_reference_and_mask_rejected(self):
        prepared=prepare(fixture());raw=prepared['expected_rgba']
        for native,replay in((raw[:-1],raw),(raw,raw[:-1])):
            with self.assertRaises(ValueError):p.compare(prepared,native,replay)
        bad=dict(prepared);bad['expected_rgba']=bytes(len(raw))
        with self.assertRaises(ValueError):p.compare(bad,raw,raw)
        bad=dict(prepared);bad['mask']=prepared['mask'].copy();bad['mask'][0,0]=not bad['mask'][0,0]
        with self.assertRaises(ValueError):p.compare(bad,raw,raw)

if __name__=='__main__':unittest.main()
