#!/usr/bin/env python3
"""Bounded real-GPU smoke test of the pinned production layer shader.
Only the output image is replaced by a float SSBO for validation/readback.
GPU timings include that write, exclude transfers, and are not headset frametimes.
"""
import argparse, hashlib, json, pathlib, subprocess
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--monado',type=pathlib.Path,required=True)
p.add_argument('--build',type=pathlib.Path,default=pathlib.Path('build/layer-filter-smoke'))
p.add_argument('--device',type=int,default=0)
p.add_argument('--spirv-opt',type=pathlib.Path,help='Match optimized runtime shader builds')
a=p.parse_args();root=pathlib.Path(__file__).resolve().parents[2]
b=a.build.resolve();b.mkdir(parents=True,exist_ok=True)
shader_dir=a.monado.resolve()/'src/xrt/auxiliary/render/shaders'
relative=pathlib.Path('src/xrt/auxiliary/render/shaders/layer.comp')
patch=root/'patches/monado/0014-desktop-layer-minification.patch'
def run(argv,**kw):
 return subprocess.run([str(x) for x in argv],check=True,timeout=90,**kw)
source=(shader_dir/'layer.comp').read_text();source_path=b/relative;source_path.parent.mkdir(parents=True,exist_ok=True)
source_path.write_text(source)
# Accept either pristine or already-patched pinned Monado source.
if 'MINIFICATION_AXIS_SAMPLES' in source:
 run(['patch','--batch','-R','-p1','-i',patch],cwd=b,stdout=subprocess.DEVNULL)
baseline=source_path.read_text()
run(['patch','--batch','-p1','-i',patch],cwd=b,stdout=subprocess.DEVNULL)
candidate=source_path.read_text()
run(['g++','-O2','-std=c++20','-Wall','-Wextra','-Werror','-Wno-missing-field-initializers',root/'fork/tests/layer_filter_bench.cpp','-lvulkan','-o',b/'bench'])
policy=b/'policy.cpp'
policy.write_text('#include "'+str(root/'server/compositor/layer_filter.h')+'"\n'
 'static_assert(wivrn::layer_filter_samples("off")==1);\n'
 'static_assert(wivrn::layer_filter_samples("balanced")==2);\n'
 'static_assert(wivrn::layer_filter_samples("quality")==4);\n'
 'static_assert(wivrn::layer_filter_samples("unknown")==1);\nint main() {}\n')
run(['g++','-std=c++20','-Wall','-Wextra','-Werror',policy,'-o',b/'policy']);run([b/'policy'])
for name,source in [('baseline',baseline),('candidate',candidate)]:
 source=source.replace('layout(set = 0, binding = 2) uniform writeonly restrict image2D target;',
  'layout(set = 0, binding = 1, std430) buffer Output {vec4 result[];};')
 source=source.replace('imageStore(target, ivec2(offset.x + ix, offset.y + iy), colour);',
  'result[iy * ubo.view.z + ix] = colour;')
 assert 'imageStore(target' not in source
 file=b/(name+'.comp');file.write_text(source)
 run(['glslangValidator','-V','--target-env','vulkan1.3','-I'+str(shader_dir),file,'-o',b/(name+'.spv')],stdout=subprocess.DEVNULL)
 if a.spirv_opt:
  run([a.spirv_opt.resolve(),'--target-env=vulkan1.3','-O',b/(name+'.spv'),'-o',b/(name+'-opt.spv')])
  (b/(name+'-opt.spv')).replace(b/(name+'.spv'))
results={}
# Reverse the order between rounds to expose drift/clock/competing-workload effects.
orders=[['baseline','off','balanced','quality'],['quality','balanced','off','baseline'],['baseline','balanced','quality','off']]
for round,order in enumerate(orders):
 for name in order:
  output=b/f'{name}-{round}';output.mkdir(exist_ok=True)
  with (b/f'{name}-{round}.json').open('w') as log:
   run([b/'bench',b/('baseline.spv' if name=='baseline' else 'candidate.spv'),{'baseline':1,'off':1,'balanced':2,'quality':4}[name],a.device,output],stdout=log)
  results[f'{name}-{round}']=json.loads((b/f'{name}-{round}.json').read_text())
for round in range(len(orders)):
 base=b/f'baseline-{round}'
 for file in base.glob('*.rgba32f'):
  data=file.read_bytes()
  assert data==(b/f'off-{round}'/file.name).read_bytes(),('off changed',file.name)
  if '0.250000' in file.name or any(f'fixture-{i}' in file.name for i in [2,3,4]):
   for mode in ['balanced','quality']:
    assert data==(b/f'{mode}-{round}'/file.name).read_bytes(),('compatibility changed',mode,file.name)
 for mode in ['balanced','quality']:
  for ref,c in zip(results[f'baseline-{round}']['cases'],results[f'{mode}-{round}']['cases']):
   if c['source_scale']==1:
    assert c['checker_rmse']<ref['checker_rmse']*.9,('no minification improvement',mode,c)
summary={'scope':__doc__,'spirv_optimizer':str(a.spirv_opt) if a.spirv_opt else None,'measured_frames_per_case':40,'warmups_per_case':5,
 'source_sha256':hashlib.sha256(baseline.encode()).hexdigest(),
 'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
 'checks':['off pixels identical to baseline','magnification pixels unchanged',
 'unpremultiplied/inverted alpha and projection unchanged','flipped stereo subimages and premultiplied alpha',
 'minified checker error reduced in quad and cylinder layers','finite output','hardware GPU (software rejected)'],
 'results':results}
summary['pixel_sha256']={str(file.relative_to(b)):hashlib.sha256(file.read_bytes()).hexdigest()
 for file in b.glob('*/*.rgba32f')}
# Keep hashes and reports rather than hundreds of megabytes of float snapshots.
for file in b.glob('*/*.rgba32f'):file.unlink()
(b/'report.json').write_text(json.dumps(summary,indent=2)+'\n')
print('PASS: production shader pixels, compatibility, minification and GPU timing; '+str(b/'report.json'))
