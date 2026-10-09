import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import unittest
from test_selection_counterfactual import fixture,sources

spec=importlib.util.spec_from_file_location('source_receipt_age_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/source_receipt_age.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def exact_fixture():
    result=dict(status='pass',analysis_kind='exact_all_integer_phase_receipt_floor_bound',
        full_window_minimum_count=1,models=[])
    for name,distinct,periods in module.q.MODELS:
        choices=[[1,1]]
        best=dict(certificate=choices,certificate_sha256=hashlib.sha256(json.dumps(choices,separators=(',',':')).encode()).hexdigest(),minimum_p95_ns=55)
        result['models'].append(dict(model=name,best_phase_ns=0,distinct_source_generations=distinct,target_periods=periods,
            minimum_p95_ns=55,evaluated={'0':best}))
    return result


class SourceReceiptAgeTests(unittest.TestCase):
    def test_new_encoded_identity_cannot_reset_same_source_receipt(self):
        known={1:dict(receipt=10,first_observed_sequence=1),2:dict(receipt=30,first_observed_sequence=2)}
        generations={1:((1,5),),2:((1,5),)};origins=module.origins(known,generations)
        row=module.sample(2,2,50,known,generations,origins)
        self.assertEqual(row['encoded_receive_age_ns'],20)
        self.assertEqual(row['known_source_receive_age_ns'],40)
        self.assertEqual(row['additional_known_source_age_ns'],20)
        self.assertEqual(row['source_origin_encoded_frame'],1)
        generations[2]=((1,6),);origins=module.origins(known,generations)
        self.assertEqual(module.sample(2,2,50,known,generations,origins)['additional_known_source_age_ns'],0)

    def test_full_source_join_and_hindsight_origin_are_explicit(self):
        traces,presented=fixture()
        for f in traces[0]['history']:
            if f.get('present') and f['frame']==2:f['receive_xr_ns']=1
        r=module.evaluate(traces,presented,sources(same_app=True),warmup_ns=0)
        self.assertEqual(r['actual_samples'][0]['additional_known_source_age_ns'],9)
        self.assertEqual(r['actual_samples'][0]['source_origin_encoded_frame'],2)
        self.assertEqual(r['warm_first_encoded']['count'],1)
        self.assertFalse(r['latency_gate_changed'])
        with self.assertRaises(ValueError):module.evaluate(traces,presented,sources(frames=(1,)),warmup_ns=0)
        known={1:dict(receipt=10,first_observed_sequence=5),2:dict(receipt=30,first_observed_sequence=2)}
        generations={1:((1,5),),2:((1,5),)}
        self.assertFalse(module.sample(2,2,50,known,generations,module.origins(known,generations))['origin_metadata_already_observed'])

    def test_host_clock_translation_cannot_change_client_age(self):
        traces,presented=fixture()
        a=module.evaluate(traces,presented,sources(same_app=True),warmup_ns=0)
        b=module.evaluate(traces,presented,sources(same_app=True,clock_offset=10**12),warmup_ns=0)
        self.assertEqual(a['actual_samples'],b['actual_samples'])
        self.assertEqual(a['warm_first_encoded'],b['warm_first_encoded'])

    def test_client_clock_translation_preserves_age_summary(self):
        traces,presented=fixture();source=sources(same_app=True)
        before=module.evaluate(traces,presented,source,warmup_ns=0)
        for row in traces+presented:
            for key in row:
                if key.endswith('_xr_ns') and key not in ('predicted_display_period_xr_ns','max_ahead_xr_ns') and row[key]:row[key]+=10**12
        for f in traces[0]['history']+traces[0]['selected']+traces[0]['closest']:
            if f.get('present'):
                for key in ('view_display_time_xr_ns','receive_xr_ns','ready_xr_ns'):f[key]+=10**12
        after=module.evaluate(traces,presented,source,warmup_ns=0)
        self.assertEqual(before['warm_first_encoded'],after['warm_first_encoded'])

    def test_canonical_multisource_identity_and_receipt_ties(self):
        known={2:dict(receipt=10,first_observed_sequence=2),1:dict(receipt=10,first_observed_sequence=1)}
        generations={1:((1,9),(2,5)),2:((2,5),(1,9))}
        first=module.origins(known,generations)
        self.assertEqual(len(first),1);self.assertEqual(next(iter(first.values()))['frame'],1)
        before=copy.deepcopy(known);module.sample(2,2,30,known,generations,first);self.assertEqual(known,before)
        for key in ((),((True,1),),((1,1),(1,2)),((1,-1),)):
            with self.assertRaises(ValueError):module.source_key(key)

    def test_future_or_invalid_receipts_and_selected_ids_rejected(self):
        with self.assertRaises(ValueError):module.origins({},{})
        for receipt in (True,0,-1,2**63):
            with self.assertRaises(ValueError):module.origins({1:dict(receipt=receipt,first_observed_sequence=1)},{1:((1,1),)})
        known={1:dict(receipt=10,first_observed_sequence=1)};g={1:((1,1),)};first=module.origins(known,g)
        for seq,frame,submit in ((1,1,9),(True,1,30),(1,2,30),(1,1,2**63)):
            with self.assertRaises(ValueError):module.sample(seq,frame,submit,known,g,first)

    def test_winning_paths_are_reaudited_without_new_optimization_claim(self):
        traces,presented=fixture()
        result=module.evaluate(traces,presented,sources(),exact_fixture(),warmup_ns=0)
        self.assertEqual(len(result['exact_phase_winning_paths_reaudited']),4)
        for path in result['exact_phase_winning_paths_reaudited']:
            self.assertFalse(path['newly_optimized_for_source_age'])
            self.assertTrue(path['direct_admission_and_original_p95_rechecked'])
            self.assertEqual(path['summary']['count'],1)

    def test_certificate_count_controls_hash_identity_and_score_fail_closed(self):
        traces,presented=fixture()
        for mutation in ('count_zero','count_bool','count_float','shortened','phase_bool','source_bool','period_bool',
                         'hash','identity','sequence','score','missing_model','duplicate_model'):
            exact=exact_fixture();model=exact['models'][0];best=model['evaluated']['0']
            if mutation=='count_zero':exact['full_window_minimum_count']=0
            elif mutation=='count_bool':exact['full_window_minimum_count']=True
            elif mutation=='count_float':exact['full_window_minimum_count']=1.0
            elif mutation=='shortened':
                exact['full_window_minimum_count']=0
                for m in exact['models']:
                    b=m['evaluated']['0'];b['certificate']=[];b['certificate_sha256']=hashlib.sha256(b'[]').hexdigest()
            elif mutation=='phase_bool':model['best_phase_ns']=False;model['evaluated']['False']=best
            elif mutation=='source_bool':model['distinct_source_generations']=0
            elif mutation=='period_bool':model['target_periods']=True
            elif mutation=='hash':best['certificate_sha256']='0'*64
            elif mutation in ('identity','sequence'):
                best['certificate'][0][1 if mutation=='identity' else 0]=999
                best['certificate_sha256']=hashlib.sha256(json.dumps(best['certificate'],separators=(',',':')).encode()).hexdigest()
            elif mutation=='score':model['minimum_p95_ns']=best['minimum_p95_ns']=54
            elif mutation=='missing_model':exact['models'].pop()
            else:exact['models'][1]=copy.deepcopy(model)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                module.evaluate(traces,presented,sources(),exact,warmup_ns=0)
