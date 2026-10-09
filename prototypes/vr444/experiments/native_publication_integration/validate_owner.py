#!/usr/bin/env python3
"""Compile actual stream quiesce with the separately owned scene hook overlay."""
import argparse,hashlib,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scene-header',type=Path,required=True);p.add_argument('--native-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 native=a.native_root.resolve();overlay=ROOT/'validation/wivrn-codecs/integration/source-overlay'
 source=[x for x in overlay.rglob('*') if x.is_file()];source += [a.scene_header,Path(__file__),HERE/'atomic-pair-validation-20261009.json']
 before={str(x):sha(x) for x in source};checks=[]
 old=json.loads((HERE/'atomic-pair-validation-20261009.json').read_text())
 with tempfile.TemporaryDirectory(prefix='owner-quiesce-syntax-') as work:
  mapping={str(native/x.relative_to(overlay)):str(x) for x in overlay.rglob('*') if x.is_file() and x.suffix!='.spv'}
  mapping[str(native/'client/scene.h')]=str(a.scene_header.resolve())
  vfs=Path(work)/'vfs.json';vfs.write_text(json.dumps({'version':0,'roots':[{'type':'file','name':n,'external-contents':v} for n,v in mapping.items()]}))
  for entry in old['checks'][4:]:
   argv=entry['argv'].copy();argv[argv.index('-ivfsoverlay')+1]=str(vfs)
   q=subprocess.run(argv,cwd=native,capture_output=True,text=True,timeout=180)
   checks.append(dict(name=entry['name'],argv=argv,exit=q.returncode,stdout=q.stdout,stderr=q.stderr))
   if q.returncode or q.stderr:raise RuntimeError(q.stderr)
 s=(overlay/'client/scenes/stream.cpp').read_text()
 body=s[s.index('void scenes::stream::prepare_exit()'):s.index('void scenes::stream::push_blit_handle')]
 ordered=['std::call_once','exit();','tracking_thread->join()','network_thread.join()','queue.lock()->waitIdle()','retired.swap(decoders)','current_blit_handles.fill(nullptr)','for (auto& item : retired) item = {}','test_stereo_generation.reset()']
 pos=[body.index(x) for x in ordered];assert pos==sorted(pos)
 assert body.index('catch (const vk::DeviceLostError&)')<body.index('retired.swap(decoders)')
 assert 'std::terminate()' in body
 stalled=s[s.index('if (state_ == state::stalled)'):s.index('auto select_test_pair',s.index('if (state_ == state::stalled)'))]
 assert stalled.index('lock.unlock()')<stalled.index('application::pop_scene()')<stalled.index('return;')
 publication=s[s.index('void scenes::stream::push_blit_handle'):s.index('bool scenes::stream::push_test_blit_pair')]
 assert publication.index('frame_lock(frames_mutex)')<publication.index('state_ == state::shutdown')<publication.index('std::rotate')
 checks.append(dict(name='actual quiesce / stalled-pop / late-publication source guards',status='pass',runtime_proof=False))
 assert before=={str(x):sha(x) for x in source}
 result=dict(status='pass',checks=checks,sources_sha256=before,device_operations=False,linked_build=False,runtime_teardown_verified=False,scope='Actual Android stream syntax with separately owned scene.h prepare_exit API plus source ordering guards. Application-hook and live lifecycle proof are separate.')
 with a.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
 print('PASS owner quiesce Android ON/OFF syntax and source guards')
if __name__=='__main__':main()
