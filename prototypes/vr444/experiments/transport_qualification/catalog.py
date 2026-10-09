#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pin transport approaches and distinguish archived bindings from fresh tests."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
PREFIX='prototypes/vr444/experiments/'
OLD_ROOT='/home/blux/spatial-bridge/.worktrees/sub15-transport/'
MILESTONES=(
 ('rgb-palette','a686dfe3b5cd9e2b6f81f90cf2d5a87e8bda6106','rgb_palette'),
 ('datagram-xor','726d7c394e5e9ec07cd78d95ca7ffe3f2bc1b5df','datagram_xor'),
 ('udp-batch','3075ff7c72e53ab83a77bd56e6fef2a50708b654','udp_batch'),
 ('transport-queue','512f3b893da4901a84fa06c2a6fd323f35f12389','transport_queue'),
 ('static-dictionary','5eb6ac1470dc1c1a7caeecd0e7877d44eb8a6440','static_dictionary'),
 ('udp-offload','74e678a1d3ca8d27e8ced4b9043f81ac4c37b30f','udp_offload'),
 ('rgb-rowfilter','2773a29a179cbab87bbeb31c4a7303eb565a77fa','rgb_rowfilter'),
 ('rgb-rowfilter-simd','895e9eff0bb0423f78ce95eae4ba0c1ea1dbaaf8','rgb_rowfilter'),
 ('xor-interleave','db48e2acf6ea936854b5e72e3db90a49ff87c0e4','xor_interleave'),
 ('rgb-rowblocks','52b355f71135096b68bc5fde413940996c748f07','rgb_rowblocks'),
 ('rowblocks-workers','260a1a2244b2381270398b65a1d3d2d282867508','rowblocks_workers'),
 ('rgb-channelplanes','afb5b5923aaa17ffbd1e92d40c1e60a17c1dde34','rgb_channelplanes'),
 ('codec-transport-budget','87236b3339ff0253ebc283810012a24e14043599','codec_transport_budget'),
 ('codec-udp-interop','f1fe2e43924de5202de6bcffc0710639714003df','codec_udp_interop'),
 ('rowblocks-encoder-workers','b8371b8be079ef81a1f32ce0804014ebed2d17e5','rowblocks_encoder_workers'),
 ('rowblocks-fused-encoder','355b3b131556dccbe68284187759699b2daebb4a','rowblocks_fused_encoder'),
 ('latency-wire-frontier','4ca8993cd9f604a4450546ba0b1fd4422c523f9a','latency_wire_frontier'),
 ('rowblocks-rgb-encoder','2066d45396b7bb93be8d6584c6bdcc1491c0f1f5','rowblocks_rgb_encoder'),
 ('rowblocks-rgb-vector','a0ee674309a7b479eb0fb79dbed86a515807c638','rowblocks_rgb_vector'),
 ('rowblocks-band-views','a55dff163e3defa5d5be2e600b8d9a7829a8f7a1','rowblocks_band_views'),
 ('rowblock-stream','7f097f3da85bfbb008d3632ffe8592e6934ffd11','rowblock_stream'),
 ('rowblock-stream-fec','8e5d1d50b77b9857817890f1c7466e90f0db38e1','rowblock_stream_fec'))
MAPS={'source_sha256','sources_sha256','source_hashes','all_native_source_sha256',
      'model_source_sha256','source_and_input_sha256','control_sha256','input_sha256','inputs_sha256'}

def need(condition,message):
    if not condition:raise ValueError(message)

def digest(raw):return hashlib.sha256(raw).hexdigest()

def hexhash(value,length):
    need(type(value) is str and re.fullmatch('[0-9a-f]{'+str(length)+'}',value) is not None,'invalid immutable hash')
    return value

def relative(name):
    need(type(name) is str and name and not name.startswith('/') and ':' not in name and '\\' not in name
        and all(part not in ('','.','..') for part in name.split('/')),'noncanonical repository path')
    return name

class Git:
    def __init__(self,root):self.root=Path(root);self.cache={}
    def blob(self,commit,path):
        key=hexhash(commit,40),relative(path)
        if key not in self.cache:
            run=subprocess.run(['git','-C',str(self.root),'cat-file','blob',commit+':'+path],capture_output=True,timeout=30)
            need(run.returncode==0,'missing pinned blob: '+path);self.cache[key]=run.stdout
        return self.cache[key]
    def files(self,commit,directory):
        hexhash(commit,40);relative(directory)
        return subprocess.check_output(['git','-C',str(self.root),'ls-tree','-r','--name-only',commit,'--',directory],text=True,timeout=30).splitlines()

def bindings(value,artifact,directory):
    """Enumerate only explicit hash maps; never turn external digests into checks."""
    result=[]
    def add(name,sha,where,base=None):
        hexhash(sha,64);need(type(name) is str,'hash-map key must be text')
        if name.startswith(OLD_ROOT):name=name[len(OLD_ROOT):]
        if base and not name.startswith('/') and '/' not in name:name=base+'/'+name
        if name.startswith(('prototypes/','validation/')):
            result.append(dict(location=where,path=relative(name),sha256=sha,scope='repository'))
        else:
            need(name and '\\' not in name and ':' not in name and all(p not in ('.','..') for p in name.split('/')),'invalid external/contextual locator')
            result.append(dict(location=where,path=name,sha256=sha,scope='external_or_contextual_unverified'))
    def walk(item,location=''):
        if isinstance(item,dict):
            for key,child in item.items():
                where=location+'/'+key
                selected=key in MAPS or key in ('documentation_sha256','result_sha256','local_sha256') or key=='sha256' and isinstance(child,dict)
                if selected:
                    base=(directory if key in ('documentation_sha256','local_sha256') else str(Path(artifact).parent) if key=='result_sha256' else None)
                    if isinstance(child,str):
                        hexhash(child,64)
                        result.append(dict(location=where,path='unpaired_digest:'+key,sha256=child,scope='external_or_contextual_unverified'))
                    elif key=='source_hashes' and isinstance(child,list):
                        for row in child:add(row['path'],row['sha256'],where)
                    else:
                        need(isinstance(child,dict) and child,'nonempty declared digest map required')
                        for name,sha in child.items():add(name,sha,where,base)
                elif isinstance(child,(dict,list)):walk(child,where)
        elif isinstance(item,list):
            for index,child in enumerate(item):
                if isinstance(child,(dict,list)):walk(child,location+'/'+str(index))
    walk(value);return result

def make(git):
    approaches=[]
    for name,commit,d in MILESTONES:
        directory=PREFIX+d;files=git.files(commit,directory);need(files,'empty approach')
        artifacts=[]
        for path in files:
            if not path.endswith(('.json','.txt','.md')):continue
            raw=git.blob(commit,path);bound=bindings(json.loads(raw),path,directory) if path.endswith('.json') else []
            differences=[]
            for item in bound:
                if item['scope']!='repository':continue
                actual=digest(git.blob(commit,item['path']))
                if actual!=item['sha256']:
                    differences.append(dict(location=item['location'],path=item['path'],recorded_sha256=item['sha256'],pinned_sha256=actual))
            artifacts.append(dict(path=path,sha256=digest(raw),bindings=len(bound),recorded_snapshot_differences=differences))
        approaches.append(dict(id=name,branch='research/sub15-20261009/'+name,commit=commit,directory=directory,
            directory_files=files,artifacts=artifacts))
    return dict(schema=1,kind='pinned_transport_research_catalog',approaches=approaches,
        headset_testing_suspended=True,promotion_eligible=False)

def verify(catalog,git):
    need(type(catalog) is dict and type(catalog.get('schema')) is int and catalog['schema']==1
        and catalog.get('kind')=='pinned_transport_research_catalog','catalog shape')
    approaches=catalog.get('approaches');expected={name:(commit,PREFIX+d) for name,commit,d in MILESTONES}
    need(type(approaches) is list and len(approaches)==len(expected) and {a.get('id') for a in approaches}==set(expected),'exact approach population required')
    rows=[];differences=[];external=[];matched=0;artifacts_checked=0
    for approach in approaches:
        name=approach['id'];commit,directory=expected[name]
        need(approach.get('commit')==commit and approach.get('directory')==directory and approach.get('branch')=='research/sub15-20261009/'+name,'pinned milestone differs')
        files=git.files(commit,directory);need(approach.get('directory_files')==files,'pinned directory inventory differs')
        paths=[p for p in files if p.endswith(('.json','.txt','.md'))]
        artifacts=approach.get('artifacts');need(type(artifacts) is list and [a['path'] for a in artifacts]==paths,'complete ordered artifact inventory required')
        row=dict(id=name,commit=commit,branch=approach['branch'],artifacts=len(artifacts),matched_repository_bindings=0,recorded_snapshot_differences=0,external_bindings_unverified=0)
        for artifact in artifacts:
            path=relative(artifact['path']);raw=git.blob(commit,path)
            need(digest(raw)==hexhash(artifact['sha256'],64),'artifact digest differs: '+path)
            bound=bindings(json.loads(raw),path,directory) if path.endswith('.json') else []
            need(type(artifact.get('bindings')) is int and artifact['bindings']==len(bound),'binding population differs')
            actual_differences=[]
            for item in bound:
                if item['scope']!='repository':
                    external.append(dict(approach=name,artifact=path,**item));row['external_bindings_unverified']+=1;continue
                actual=digest(git.blob(commit,item['path']))
                if actual==item['sha256']:matched+=1;row['matched_repository_bindings']+=1
                else:
                    change=dict(location=item['location'],path=item['path'],recorded_sha256=item['sha256'],pinned_sha256=actual)
                    actual_differences.append(change);differences.append(dict(approach=name,artifact=path,**change));row['recorded_snapshot_differences']+=1
            need(artifact.get('recorded_snapshot_differences')==actual_differences,'recorded source differences omitted/changed')
            artifacts_checked+=1
        rows.append(row)
    return dict(schema=1,status='historical_snapshot_differences' if differences else 'pass',
        artifact_integrity='pass',kind='archived_git_blob_and_declared_binding_audit',approaches=rows,
        approach_count=len(rows),artifacts_verified=artifacts_checked,matched_repository_bindings=matched,
        recorded_snapshot_differences=differences,external_or_contextual_bindings_unverified=external,
        fresh_execution=False,remote_refs_verified=False,external_files_verified=False,native_build_qualified=False,
        headset_operations=False,latency_gate_pass=False,promotion_eligible=False,
        limitations=['All directory notes/JSON artifacts are inventoried, including inherited/superseded receipts; no receipt is silently promoted to a later source snapshot.',
            'Differences remain explicit even for documentation edits. A matching artifact hash is not a fresh correctness or performance run.',
            'External binaries, corpus files and contextual hashes are not verified by this Git-only audit.',
            'Only explicitly recognized digest maps are audited; no complete historical dependency coverage is inferred.'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--generate',action='store_true')
    parser.add_argument('--catalog',type=Path,default=HERE/'catalog.json');parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();git=Git(ROOT)
    if args.generate:
        data=make(git)
        with args.catalog.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
    raw=args.catalog.read_bytes();result=verify(json.loads(raw),git);need(args.catalog.read_bytes()==raw,'catalog changed during audit')
    result['catalog_sha256']=digest(raw)
    with args.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(dict(status=result['status'],approaches=result['approach_count'],artifacts=result['artifacts_verified'],
        matched=result['matched_repository_bindings'],differences=len(result['recorded_snapshot_differences']))))

if __name__=='__main__':main()
