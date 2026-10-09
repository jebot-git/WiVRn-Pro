import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

HERE=Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/compositor_qualification'
spec=importlib.util.spec_from_file_location('compositor_history_test',HERE/'history.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
spec=importlib.util.spec_from_file_location('compositor_run_test',HERE/'run.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def fixture(receipt=None):
    source=b'original source\n'
    receipt=receipt or dict(status='pass',sources_sha256={'source.py':h.digest(source)})
    raw=json.dumps(receipt).encode();commit='a'*40
    artifact=dict(path='receipt.json',kind='validation',sha256=h.digest(raw),source_bindings=len(h.bindings(receipt)))
    catalog=dict(schema=1,kind='pinned_compositor_research_catalog',approaches=[
        dict(id=name,branch='research/sub15-20261009/'+name,commit=commit,artifacts=[copy.deepcopy(artifact)]) for name in h.APPROACHES])
    blobs={'receipt.json':raw,'source.py':source}
    def read(c,path):
        if c!=commit or path not in blobs:raise ValueError('missing blob')
        return blobs[path]
    return catalog,blobs,read


class CompositorQualificationTests(unittest.TestCase):
    def test_complete_catalog_checks_pinned_sources_without_execution_claim(self):
        catalog,_,read=fixture();result=h.verify(catalog,read)
        self.assertEqual(result['approach_count'],27)
        self.assertEqual(result['repository_source_bindings_verified'],27)
        for key in ('fresh_test_execution','external_artifacts_verified','headset_operations','native_build_qualified','latency_gate_pass','promotion_eligible'):
            self.assertIs(result[key],False)

    def test_missing_duplicate_or_wrong_branch_milestone_rejected(self):
        for mutation in ('missing','duplicate','branch','short_commit','empty_artifacts'):
            catalog,_,read=fixture()
            if mutation=='missing':catalog['approaches'].pop()
            elif mutation=='duplicate':catalog['approaches'][-1]=copy.deepcopy(catalog['approaches'][0])
            elif mutation=='branch':catalog['approaches'][0]['branch']='other'
            elif mutation=='short_commit':catalog['approaches'][0]['commit']='a'*8
            else:catalog['approaches'][0]['artifacts']=[]
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):h.verify(catalog,read)

    def test_changed_artifact_source_or_binding_population_rejected(self):
        for mutation in ('artifact','source','zero_count','bool_count','extra_count','notes_only'):
            catalog,blobs,read=fixture();a=catalog['approaches'][0]['artifacts'][0]
            if mutation=='artifact':blobs['receipt.json']+=b' '
            elif mutation=='source':blobs['source.py']+=b' '
            elif mutation=='zero_count':a['source_bindings']=0
            elif mutation=='bool_count':a['source_bindings']=True
            elif mutation=='extra_count':a['source_bindings']=2
            else:a['kind']='notes';a['source_bindings']=0
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):h.verify(catalog,read)

    def test_malformed_source_map_digests_do_not_disappear(self):
        for bad in (None,True,1,{},[], 'x'*64,'a'*63):
            with self.subTest(bad=bad),self.assertRaises(ValueError):h.bindings(dict(sources_sha256={'source.py':bad}))
        for bad in ({},[],None):
            with self.assertRaises(ValueError):h.bindings(dict(sources_sha256=bad))
        for path in ('/etc/passwd','../file','a/../b','a//b','a:b','a\\b','./a',''):
            with self.subTest(path=path),self.assertRaises(ValueError):h.relative_path(path)

    def test_external_sources_are_explicitly_unverified_not_read(self):
        receipt=dict(status='pass',sources_sha256={'source.py':h.digest(b'original source\n'),'/tmp/old-probe':'b'*64})
        catalog,_,read=fixture(receipt);result=h.verify(catalog,read)
        item=result['approaches'][0]['artifacts'][0]
        self.assertEqual(item['source_bindings_verified'],1)
        self.assertEqual(item['external_source_bindings_unverified'][0]['path'],'/tmp/old-probe')
        receipt['sources_sha256'].pop('source.py');catalog,_,read=fixture(receipt)
        with self.assertRaises(ValueError):h.verify(catalog,read)

    def test_historical_layout_gap_remains_incomplete_and_exactly_bounded(self):
        catalog,blobs,read=fixture();old=h.digest(blobs['source.py']);blobs['source.py']=b'layout correction\n';new=h.digest(blobs['source.py'])
        gap=dict(path='source.py',recorded_sha256=old,committed_sha256=new,reason='Layout changed after historical receipt.')
        for row in catalog['approaches']:row['artifacts'][0]['known_source_gaps']=[copy.deepcopy(gap)]
        result=h.verify(catalog,read)
        self.assertEqual(result['status'],'incomplete_source_history')
        self.assertEqual(result['repository_source_integrity'],'incomplete')
        self.assertEqual(result['repository_source_bindings_verified'],0)
        for key in ('recorded_sha256','committed_sha256','reason'):
            changed=copy.deepcopy(catalog);changed['approaches'][0]['artifacts'][0]['known_source_gaps'][0][key]=''
            with self.assertRaises(ValueError):h.verify(changed,read)
        blobs['source.py']=b'original source\n'
        with self.assertRaises(ValueError):h.verify(catalog,read)

    def test_figures_bind_final_render_without_rewriting_earlier_receipt(self):
        value=dict(kind='figure_render_review',sha256={'plot.py':'a'*64,'render.png':'b'*64})
        self.assertEqual(len(h.bindings(value)),2)
        value['sha256']['render.png']=None
        with self.assertRaises(ValueError):h.bindings(value)

    def test_python_suite_requires_real_execution_without_skips(self):
        def run(text,code=0):return subprocess.CompletedProcess(['python'],code,'',text)
        self.assertEqual(r.python_tests(run('\nRan 12 tests in 1.002s\n\nOK\n')),12)
        for text in ('\nRan 0 tests in 0.0s\n\nOK\n','\nRan 12 tests in 1.0s\n\nOK (skipped=12)\n','OK\n','\nRan 12 tests in 1.0s\n\nFAILED\n'):
            with self.assertRaises(ValueError):r.python_tests(run(text))
        with self.assertRaises(ValueError):r.python_tests(run('\nRan 12 tests in 1.0s\n\nOK\n',1))


class FreshHostGateTests(unittest.TestCase):
    def test_host_success_requires_zero_exit_no_diagnostics_and_full_marker(self):
        def result(stdout='complete\n',stderr='',code=0):return subprocess.CompletedProcess(['owned-test'],code,stdout,stderr)
        r.host.require_result(result(),r'complete\n')
        for value in (result(stdout=''),result(stdout='complete\nextra'),result(stderr='runtime error'),result(code=1)):
            with self.assertRaises(ValueError):r.host.require_result(value,r'complete\n')

    def test_every_fixed_host_and_thread_stage_is_required(self):
        rows=[dict(id=value) for value in r.host.expected_ids()]
        self.assertEqual(len(rows),10);r.host.check_population(rows)
        for changed in (rows[:-1],rows+[rows[-1]],list(reversed(rows))):
            with self.assertRaises(ValueError):r.host.check_population(changed)


    def test_pacer_header_test_explicitly_enables_its_compile_guard(self):
        for name,source,_ in r.host.PROGRAMS:
            command=r.host.compiler_argv('/usr/bin/clang++',source,'/tmp/owned-test','address,undefined')
            self.assertEqual('-DWIVRN_CODEC_TESTS=1' in command,name=='pacer')
            self.assertIn('-fsanitize=address,undefined',command)
            self.assertNotIn('-DNDEBUG',command)
