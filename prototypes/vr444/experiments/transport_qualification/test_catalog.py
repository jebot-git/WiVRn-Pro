#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import copy
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import catalog as c
import run

class FakeGit:
    def __init__(self):
        self.data={}
        for name,commit,d in c.MILESTONES:
            directory=c.PREFIX+d;source=directory+'/source.cpp'
            raw=('source for '+name).encode();receipt={'source_sha256':{source:c.digest(raw)}}
            self.data[commit,source]=raw
            self.data[commit,directory+'/README.txt']=b'notes'
            self.data[commit,directory+'/results/receipt.json']=json.dumps(receipt).encode()
    def files(self,commit,directory):return sorted(path for sha,path in self.data if sha==commit and path.startswith(directory+'/'))
    def blob(self,commit,path):
        c.hexhash(commit,40);c.relative(path)
        return self.data[commit,path]

class CatalogTests(unittest.TestCase):
    def setUp(self):self.git=FakeGit();self.catalog=c.make(self.git)
    def test_complete_pinned_inventory(self):
        result=c.verify(self.catalog,self.git)
        self.assertEqual(result['status'],'pass');self.assertEqual(result['approach_count'],22)
        self.assertEqual(result['artifacts_verified'],44);self.assertEqual(result['matched_repository_bindings'],22)
        self.assertFalse(result['fresh_execution']);self.assertFalse(result['promotion_eligible'])
    def test_omitted_duplicate_or_changed_approach_rejected(self):
        for mutation in ('omit','duplicate','commit','branch','directory'):
            value=copy.deepcopy(self.catalog)
            if mutation=='omit':value['approaches'].pop()
            elif mutation=='duplicate':value['approaches'][-1]=value['approaches'][0]
            elif mutation=='commit':value['approaches'][0]['commit']='0'*40
            else:value['approaches'][0][mutation]='changed'
            with self.assertRaises(ValueError):c.verify(value,self.git)
    def test_missing_artifact_or_directory_entry_rejected(self):
        for field in ('artifacts','directory_files'):
            value=copy.deepcopy(self.catalog);value['approaches'][0][field].pop()
            with self.assertRaises(ValueError):c.verify(value,self.git)
    def test_artifact_digest_and_binding_count_rejected(self):
        for field,changed in (('sha256','0'*64),('bindings',True),('bindings',99)):
            value=copy.deepcopy(self.catalog);value['approaches'][0]['artifacts'][0][field]=changed
            with self.assertRaises(ValueError):c.verify(value,self.git)
    def test_source_difference_remains_incomplete_and_cannot_be_hidden(self):
        _,commit,d=c.MILESTONES[0];self.git.data[commit,c.PREFIX+d+'/source.cpp']=b'changed source'
        with self.assertRaises(ValueError):c.verify(self.catalog,self.git)
        value=c.make(self.git);result=c.verify(value,self.git)
        self.assertEqual(result['status'],'historical_snapshot_differences');self.assertEqual(len(result['recorded_snapshot_differences']),1)
        self.assertFalse(result['promotion_eligible'])
        target=next(a for a in value['approaches'][0]['artifacts'] if a['recorded_snapshot_differences'])
        target['recorded_snapshot_differences']=[]
        with self.assertRaises(ValueError):c.verify(value,self.git)
    def test_invented_difference_rejected(self):
        value=copy.deepcopy(self.catalog);value['approaches'][0]['artifacts'][0]['recorded_snapshot_differences']=[{}]
        with self.assertRaises(ValueError):c.verify(value,self.git)
    def test_external_and_unpaired_hashes_are_unverified(self):
        bound=c.bindings({'inputs_sha256':{'/tmp/not-present':'1'*64},'nested':{'source_sha256':'2'*64}},'prototypes/test.json','prototypes')
        self.assertEqual(len(bound),2);self.assertTrue(all(b['scope']=='external_or_contextual_unverified' for b in bound))
    def test_worktree_absolute_binding_resolves_to_repo(self):
        bound=c.bindings({'source_sha256':{c.OLD_ROOT+'prototypes/a.py':'1'*64}},'prototypes/test.json','prototypes')
        self.assertEqual(bound[0]['path'],'prototypes/a.py');self.assertEqual(bound[0]['scope'],'repository')
    def test_malformed_paths_and_hashes_reject(self):
        for path in ('../a','/absolute','prototypes//a','prototypes/./a','prototypes/../a','x:y','x\\y'):
            with self.assertRaises(ValueError):c.relative(path)
        for sha in ('a'*63,'A'*64,True,None):
            with self.assertRaises(ValueError):c.hexhash(sha,64)
    def test_python_exit_skip_empty_and_forged_count_reject(self):
        okay=subprocess.CompletedProcess([],0,'','\nRan 7 tests in 0.100s\n\nOK\n')
        self.assertEqual(run.test_count(okay),7)
        for code,stderr in ((1,okay.stderr),(0,'\nRan 0 tests in 0.100s\n\nOK\n'),(0,'\nRan 7 tests in 0.100s\n\nOK (skipped=1)\n'),(0,'OK\n')):
            with self.assertRaises(ValueError):run.test_count(subprocess.CompletedProcess([],code,'',stderr))
    def test_archive_rejects_traversal_links_and_other_prefixes(self):
        for name,kind in (('../escape',tarfile.REGTYPE),('prototypes/link',tarfile.SYMTYPE),('other/a',tarfile.REGTYPE)):
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w') as archive:
                info=tarfile.TarInfo(name);info.type=kind
                if kind==tarfile.SYMTYPE:info.linkname='/tmp/outside'
                archive.addfile(info,io.BytesIO())
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):run.extract(buffer.getvalue(),tmp)
                self.assertFalse(list(Path(tmp).iterdir()))
    def test_snapshot_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a').write_text('source');(root/'b').symlink_to(root/'a')
            with self.assertRaises(ValueError):run.snapshot(root)

if __name__=='__main__':unittest.main()
