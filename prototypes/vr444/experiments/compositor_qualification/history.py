#!/usr/bin/env python3
"""Verify pinned compositor artifacts and their in-repository source bindings."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OLD_ROOT='/home/blux/spatial-bridge/.worktrees/sub15-compositor/'
OVERLAY='validation/wivrn-codecs/integration/source-overlay/'
SOURCE_MAPS={'sources_sha256','source_sha256','analyzer_sha256'}
VIRTUAL_MAPS={'virtual_sources_sha256','virtual_replacements_sha256'}
APPROACHES=('compositor','compositor-attempts','compositor-pacing','compositor-reserve',
    'compositor-pacer-trace','compositor-selection-audit','compositor-oracle','compositor-timeline',
    'compositor-pacer-init','compositor-pacer-capture','compositor-target-envelope','compositor-supply-phase',
    'compositor-full-count','compositor-cardinality','compositor-publication-ledger','compositor-publication-capture',
    'compositor-phase-frontier','compositor-target-admission','compositor-source-novelty','compositor-quantile-bound',
    'compositor-host-timeline','compositor-receipt-floor','compositor-exact-phase','compositor-source-receipt-age')


def digest(data):return hashlib.sha256(data).hexdigest()


def sha256(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('exact SHA-256 digest required')
    return value


def relative_path(value):
    if (type(value) is not str or not value or value.startswith('/') or ':' in value or '\\' in value
            or any(p in ('','.','..') for p in value.split('/'))):
        raise ValueError('canonical repository-relative path required')
    return str(PurePosixPath(value))


def source_path(value,virtual=False):
    if type(value) is not str:raise ValueError('source path must be text')
    if value.startswith(OLD_ROOT):value=value[len(OLD_ROOT):]
    result=relative_path(value)
    return OVERLAY+result if virtual else result


def source_binding(path,hash_value,location,virtual=False):
    hash_value=sha256(hash_value)
    if type(path) is not str:raise ValueError('source path must be text')
    if path.startswith('/') and not path.startswith(OLD_ROOT) and not virtual:
        if any(p in ('','.','..') for p in path[1:].split('/')) or ':' in path or '\\' in path:
            raise ValueError('canonical external path required')
        return dict(location=location,path=path,sha256=hash_value,scope='external_unverified')
    return dict(location=location,path=source_path(path,virtual),sha256=hash_value,scope='repository')


def bindings(value):
    """Only declared source maps are asserted; external digest fields stay historical."""
    result=[]
    def walk(item,location):
        if isinstance(item,dict):
            for key,child in item.items():
                where=location+'/'+key
                if key in SOURCE_MAPS|VIRTUAL_MAPS or (not location and key=='sha256' and item.get('kind')=='figure_render_review'):
                    if type(child) is not dict or not child:raise ValueError('nonempty source digest map required: '+where)
                    for path,hash_value in child.items():
                        result.append(source_binding(path,hash_value,where,key in VIRTUAL_MAPS))
                elif isinstance(child,(dict,list)):walk(child,where)
        elif isinstance(item,list):
            for i,child in enumerate(item):
                if isinstance(child,(dict,list)):walk(child,location+'/'+str(i))
    walk(value,'')
    return result


class GitBlobs:
    def __init__(self,root):self.root=Path(root);self.cache={}
    def __call__(self,commit,path):
        if type(commit) is not str or re.fullmatch('[0-9a-f]{40}',commit) is None:raise ValueError('full immutable Git commit required')
        path=relative_path(path);key=(commit,path)
        if key not in self.cache:
            run=subprocess.run(['git','-C',str(self.root),'cat-file','blob',commit+':'+path],capture_output=True,timeout=30)
            if run.returncode:raise ValueError('missing pinned Git blob: '+commit+':'+path)
            self.cache[key]=run.stdout
        return self.cache[key]


def verify(catalog,read_blob):
    if (type(catalog) is not dict or type(catalog.get('schema')) is not int or catalog['schema']!=1
            or catalog.get('kind')!='pinned_compositor_research_catalog'
            or type(catalog.get('approaches')) is not list):raise ValueError('invalid compositor catalog')
    approaches=catalog['approaches']
    if len(approaches)!=len(APPROACHES) or {a.get('id') for a in approaches}!=set(APPROACHES):
        raise ValueError('required compositor approach population differs')
    rows=[];gaps=[]
    for approach in approaches:
        commit=approach['commit'];artifacts=approach['artifacts']
        if (approach.get('branch')!='research/sub15-20261009/'+approach['id']
                or type(commit) is not str or re.fullmatch('[0-9a-f]{40}',commit) is None
                or type(artifacts) is not list or not artifacts):raise ValueError('invalid pinned approach')
        if len({a['path'] for a in artifacts})!=len(artifacts):raise ValueError('duplicate approach artifact')
        verified=[];source_count=0;receipt_count=0
        for artifact in artifacts:
            path=relative_path(artifact['path']);raw=read_blob(commit,path)
            if digest(raw)!=sha256(artifact['sha256']):raise ValueError('pinned artifact hash differs: '+path)
            kind=artifact.get('kind');count=artifact.get('source_bindings')
            if kind not in ('notes','analysis','validation','figure','figure_receipt') or type(count) is not int or count<0:
                raise ValueError('invalid artifact kind/source count')
            bound=[];artifact_gaps=[]
            declared_gaps=artifact.get('known_source_gaps',[])
            if type(declared_gaps) is not list or len({g['path'] for g in declared_gaps})!=len(declared_gaps):
                raise ValueError('invalid known source-gap population')
            gap_map={relative_path(g['path']):g for g in declared_gaps}
            for gap in declared_gaps:
                sha256(gap['recorded_sha256']);sha256(gap['committed_sha256'])
                if type(gap.get('reason')) is not str or not gap['reason'].strip():raise ValueError('known gap requires an explicit explanation')
            if kind in ('analysis','validation','figure_receipt'):
                value=json.loads(raw)
                if value.get('status')!='pass':raise ValueError('cataloged positive receipt does not pass: '+path)
                bound=bindings(value)
                if not any(item['scope']=='repository' for item in bound):raise ValueError('positive historical receipt has no repository source bindings: '+path)
                for item in bound:
                    if item['scope']=='external_unverified':continue
                    actual=digest(read_blob(commit,item['path']))
                    if actual!=item['sha256']:
                        gap=gap_map.get(item['path'])
                        if gap is None or gap['recorded_sha256']!=item['sha256'] or gap['committed_sha256']!=actual:
                            raise ValueError('historical source differs: '+path+' -> '+item['path'])
                        artifact_gaps.append(dict(artifact=path,commit=commit,**gap))
                receipt_count+=1
            if {g['path'] for g in artifact_gaps}!=set(gap_map):raise ValueError('declared source gap did not occur exactly')
            gaps.extend(artifact_gaps)
            if count!=len(bound):raise ValueError('historical source binding population differs: '+path)
            checked=sum(item['scope']=='repository' for item in bound)-len(artifact_gaps)
            source_count+=checked
            verified.append(dict(path=path,kind=kind,sha256=artifact['sha256'],
                source_bindings_verified=checked,known_source_gaps=artifact_gaps,
                external_source_bindings_unverified=[item for item in bound if item['scope']=='external_unverified']))
        if receipt_count<1:raise ValueError('approach omits a source-bound positive receipt')
        rows.append(dict(id=approach['id'],commit=commit,artifacts=verified,source_bindings_verified=source_count))
    return dict(schema=1,status='incomplete_source_history' if gaps else 'pass',kind='historical_git_blob_integrity_only',
        archived_artifact_integrity='pass',repository_source_integrity='incomplete' if gaps else 'pass',known_source_gaps=gaps,
        approaches=rows,approach_count=len(rows),artifacts_verified=sum(len(r['artifacts']) for r in rows),
        repository_source_bindings_verified=sum(r['source_bindings_verified'] for r in rows),
        fresh_test_execution=False,external_artifacts_verified=False,headset_operations=False,
        native_build_qualified=False,latency_gate_pass=False,promotion_eligible=False,
        limitations=[
            'Checks archived artifact bytes and declared repository/virtual-overlay source hashes at each immutable Git commit.',
            'Does not rerun historical tests or verify compiler binaries, native build caches, temporary executables, acquisition files, devices or remote branch state.',
            'A historical pass remains limited to the original receipt scope. Later source changes do not inherit earlier execution.',
            'The original failed run remains failed; diagnostic reanalysis does not replace qualification.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--catalog',type=Path,default=HERE/'catalog.json')
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    raw=args.catalog.read_bytes();result=verify(json.loads(raw),GitBlobs(ROOT))
    if args.catalog.read_bytes()!=raw:raise ValueError('catalog changed during verification')
    result['catalog_sha256']=digest(raw)
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k.endswith('_verified') or k=='approach_count'}))


if __name__=='__main__':main()
