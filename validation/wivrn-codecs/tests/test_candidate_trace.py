import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('candidate_trace', Path(__file__).resolve().parents[1]/'integration/candidate_trace.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    def frame(eye, number, target):
        return dict(decoder=eye, present=True, frame=number, warp=number+10,
                    view_display_time_xr_ns=target, receive_xr_ns=10, ready_xr_ns=20, alpha=False)
    history = [dict(decoder=i//3, slot=i%3, present=False) for i in range(9)]
    for eye in range(2):
        history[eye*3] = frame(eye, 1, 100) | dict(slot=0)
        history[eye*3+1] = frame(eye, 2, 120) | dict(slot=1)
    selected = [frame(0, 1, 100), frame(1, 1, 100), dict(decoder=2, present=False)]
    r = dict(schema=1, sequence=1, selection_phase='early', wait_frame_begin_xr_ns=30,
             wait_frame_end_xr_ns=40, render_entry_xr_ns=41, selection_begin_xr_ns=42,
             selection_end_xr_ns=50, predicted_display_xr_ns=100, end_frame_return_xr_ns=60,
             alpha_required=False, projection_submitted=True, reason='closest_target',
             repeat_previous=False, history=history, selected=selected)
    p = {k:r[k] for k in ('selection_phase','render_entry_xr_ns','selection_begin_xr_ns',
                          'selection_end_xr_ns','predicted_display_xr_ns','end_frame_return_xr_ns')}
    p.update(left=1, right=1, left_warp=11, right_warp=11, submit_xr_ns=65,
             render_fence_begin_xr_ns=52,render_fence_end_xr_ns=53,
             left_receive_xr_ns=10, right_receive_xr_ns=10, left_decode_ready_xr_ns=20,
             right_decode_ready_xr_ns=20, candidate_trace_records=1, candidate_trace_dropped=0,
             candidate_trace_write_ok=True, candidate_trace_write_ns=10000)
    return r, p


def bounded_fixture():
    r, p = fixture()
    r.update(schema=2, selection_policy='bounded-newest', predicted_display_period_xr_ns=20,
             previous_predicted_display_xr_ns=80, max_ahead_xr_ns=20, selected_alpha_required=False,
             closest=copy.deepcopy(r['selected']), previous=[dict(decoder=i,present=False) for i in range(3)],
             chosen_slots=[1,1,-1], eligible_left_slots=[True,True,False], reason='bounded_newest')
    r['selected'] = [dict(r['history'][1]), dict(r['history'][4]), dict(decoder=2,present=False)]
    for f in r['selected']:
        f.pop('slot',None)
    p.update(left=2,right=2,left_warp=12,right_warp=12)
    for key in ('selection_policy','predicted_display_period_xr_ns','previous_predicted_display_xr_ns','max_ahead_xr_ns'):
        p[key]=r[key]
    return r,p


def fallback_after(r, p, previous_target=100):
    """Second submission retaining the first pair across an optional empty render."""
    r2, p2 = copy.deepcopy(r), copy.deepcopy(p)
    r2.update(sequence=2, reason='no_complete_fallback', repeat_previous=True,
              alpha_required=False, previous=copy.deepcopy(r['selected']),
              closest=[dict(decoder=i, present=False) for i in range(3)],
              chosen_slots=[-1]*3, eligible_left_slots=[False]*3,
              history=[dict(decoder=i//3, slot=i%3, present=False) for i in range(9)])
    for key in ('wait_frame_begin_xr_ns', 'wait_frame_end_xr_ns', 'render_entry_xr_ns',
                'selection_begin_xr_ns', 'selection_end_xr_ns', 'end_frame_return_xr_ns',
                'predicted_display_xr_ns'):
        r2[key] += 100
        if key in p2:
            p2[key] += 100
    r2['previous_predicted_display_xr_ns'] = p2['previous_predicted_display_xr_ns'] = previous_target
    r2['max_ahead_xr_ns'] = p2['max_ahead_xr_ns'] = min(20, 200-previous_target)
    p2.update(candidate_trace_records=2, submit_xr_ns=165,
              render_fence_begin_xr_ns=152, render_fence_end_xr_ns=153)
    return r2, p2


class CandidateTraceTests(unittest.TestCase):
    def test_schema2_intervening_render_target_preserves_submission_contract(self):
        for prior in (100, 101, 150, 199):
            with self.subTest(prior=prior):
                r, p = bounded_fixture()
                r2, p2 = fallback_after(r, p, prior)
                result = module.evaluate([r, r2], [p, p2])
                self.assertEqual(result['status'], 'pass', result['errors'])
                self.assertEqual(result['unrecorded_render_target_gaps'], int(prior > 100))
                self.assertEqual(result['render_attempt_coverage'], 'not_recorded')
                self.assertIsNone(result['skipped_render_count'])
                self.assertIsNone(result['skipped_render_reasons'])
                self.assertFalse(result['promotion_eligible'])

    def test_schema2_gap_cannot_hide_prior_target_regression_or_current_target(self):
        for prior in (0, 99, 200, 201, True, 150.0, -1, 1 << 63):
            with self.subTest(prior=prior):
                r, p = bounded_fixture()
                r2, p2 = fallback_after(r, p)
                r2['previous_predicted_display_xr_ns'] = p2['previous_predicted_display_xr_ns'] = prior
                self.assertEqual(module.evaluate([r, r2], [p, p2])['status'], 'fail')

    def test_schema2_first_prior_target_must_be_zero_or_before_current_target(self):
        for prior in (0, 99):
            r, p = bounded_fixture()
            r['selection_policy'] = p['selection_policy'] = 'closest-target'
            r.update(reason='closest_target', selected=copy.deepcopy(r['closest']), chosen_slots=[0, 0, -1])
            p.update(left=1, right=1, left_warp=11, right_warp=11)
            r['previous_predicted_display_xr_ns'] = p['previous_predicted_display_xr_ns'] = prior
            r['max_ahead_xr_ns'] = p['max_ahead_xr_ns'] = min(20, 100-prior) if prior else 0
            self.assertEqual(module.evaluate([r], [p])['status'], 'pass')
        for prior in (100, 101):
            r, p = bounded_fixture()
            r['previous_predicted_display_xr_ns'] = p['previous_predicted_display_xr_ns'] = prior
            r['max_ahead_xr_ns'] = p['max_ahead_xr_ns'] = 0
            self.assertEqual(module.evaluate([r], [p])['status'], 'fail')

    def test_schema2_gap_cannot_hide_an_unobserved_selection_transition(self):
        r, p = bounded_fixture()
        r2, p2 = fallback_after(r, p, 150)
        for frames in (r2['previous'], r2['selected']):
            for f in frames[:2]:
                f.update(frame=3, warp=13, view_display_time_xr_ns=130)
        p2.update(left=3, right=3, left_warp=13, right_warp=13)
        result = module.evaluate([r, r2], [p, p2])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('previous selection snapshot does not match prior trace', result['errors'])

    def test_schema2_gap_preserves_bound_join_counter_and_metadata_checks(self):
        for mutation in ('bound', 'prior_join', 'counter', 'metadata', 'time_overlap', 'missing_trace'):
            with self.subTest(mutation=mutation):
                r, p = bounded_fixture()
                r2, p2 = fallback_after(r, p, 150)
                traces, presents = [r, r2], [p, p2]
                if mutation == 'bound':
                    r2['max_ahead_xr_ns'] = p2['max_ahead_xr_ns'] = 21
                elif mutation == 'prior_join':
                    p2['previous_predicted_display_xr_ns'] = 151
                elif mutation == 'counter':
                    p2['candidate_trace_records'] = 3
                elif mutation == 'metadata':
                    r2['previous'][0]['ready_xr_ns'] += 1
                elif mutation == 'time_overlap':
                    r2['wait_frame_begin_xr_ns'] = 59
                else:
                    traces.pop(0)
                    traces[0]['sequence'] = 1
                self.assertEqual(module.evaluate(traces, presents)['status'], 'fail')

    def test_schema2_bounded_choice_replays_actual_eligibility(self):
        r,p = bounded_fixture()
        result=module.evaluate([r],[p],selection_policy='bounded-newest')
        self.assertEqual(result['status'],'pass',result['errors'])
        self.assertEqual(result['higher_frame_id_available'],0)
        self.assertFalse(result['promotion_eligible'])

    def test_schema2_policy_bound_slots_and_metadata_fail_closed(self):
        for key,value in (('max_ahead_xr_ns',21),('max_ahead_xr_ns',True),
                          ('predicted_display_period_xr_ns',-1),('selected_alpha_required',True),
                          ('chosen_slots',[0,0,-1]),('chosen_slots',[True,1,-1]),
                          ('eligible_left_slots',[True,False,False]),('selection_policy','latest')):
            r,p=bounded_fixture();r[key]=value
            if key in p:p[key]=value
            self.assertEqual(module.evaluate([r],[p])['status'],'fail',key)
        r,p=bounded_fixture();r['previous']=[]
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')

    def test_future_bound_and_previous_choice_cannot_be_forged(self):
        r,p=bounded_fixture()
        for frame in (r['history'][1],r['history'][4],r['selected'][0],r['selected'][1]):
            frame['view_display_time_xr_ns']=121
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        r,p=bounded_fixture()
        r['previous']=copy.deepcopy(r['selected'])
        for frame in r['previous'][:2]:frame.update(frame=3,warp=13,view_display_time_xr_ns=130)
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')

    def test_schema2_closest_default_and_requested_policy_binding(self):
        r,p=bounded_fixture();r['selection_policy']=p['selection_policy']='closest-target'
        r.update(reason='closest_target',selected=copy.deepcopy(r['closest']),chosen_slots=[0,0,-1])
        p.update(left=1,right=1,left_warp=11,right_warp=11)
        self.assertEqual(module.evaluate([r],[p],selection_policy='closest-target')['status'],'pass')
        self.assertEqual(module.evaluate([r],[p],selection_policy='bounded-newest')['status'],'fail')
        r,p=fixture()
        self.assertEqual(module.evaluate([r],[p],selection_policy='bounded-newest')['status'],'fail')

    def test_bounded_policy_cannot_run_without_diagnostic_trace(self):
        self.assertEqual(module.evaluate([],[],False,selection_policy='bounded-newest')['status'],'fail')

    def test_schema2_first_previous_must_be_coherent_and_repeat_flag_honest(self):
        r,p=bounded_fixture();r['previous'][0]=copy.deepcopy(r['selected'][0])
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')

    def test_schema2_default_cannot_qualify_mismatched_eye_targets(self):
        r,p=bounded_fixture();r['selection_policy']=p['selection_policy']='closest-target'
        r.update(reason='closest_target',selected=copy.deepcopy(r['closest']),chosen_slots=[0,0,-1])
        p.update(left=1,right=1,left_warp=11,right_warp=11)
        for f in (r['selected'][1],r['closest'][1],r['history'][3]):f['view_display_time_xr_ns']=101
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        r,p=bounded_fixture();r['previous']=copy.deepcopy(r['selected'])
        r['eligible_left_slots']=[False,True,False]
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        r['repeat_previous']=True
        self.assertEqual(module.evaluate([r],[p])['status'],'pass')
        r,p=bounded_fixture();p['max_ahead_xr_ns']=20.0
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        r,p=bounded_fixture();r['projection_submitted']=False
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')

    def test_observed_newer_frame_is_opportunity_not_speedup(self):
        r,p = fixture(); x = module.evaluate([r],[p])
        self.assertEqual(x['errors'], [])
        self.assertEqual(x['status'], 'pass')
        self.assertEqual(x['higher_frame_id_available'], 1)
        self.assertFalse(x['promotion_eligible'])
        self.assertEqual(x['distributions']['trace_write_ms']['p50'], .01)

    def test_tie_retains_existing_history_order(self):
        r,p = fixture(); r['predicted_display_xr_ns'] = p['predicted_display_xr_ns'] = 110
        self.assertEqual(module.evaluate([r],[p])['status'], 'pass')

    def test_newer_orphan_is_not_a_complete_candidate(self):
        r,p = fixture(); r['history'][4] = dict(decoder=1,slot=1,present=False)
        x = module.evaluate([r],[p]); self.assertEqual(x['status'],'pass')
        self.assertEqual(x['higher_frame_id_available'], 0)

    def test_choice_identity_and_metadata_are_verified(self):
        for where,key,value in [('trace','reason','newest'),('present','left',2),
                                ('selected','warp',99),('selected','view_display_time_xr_ns',99),
                                ('history','ready_xr_ns',51)]:
            r,p=fixture(); target={'trace':r,'present':p,'selected':r['selected'][0],'history':r['history'][0]}[where]
            target[key]=value
            self.assertEqual(module.evaluate([r],[p])['status'],'fail')

    def test_missing_order_nonfinite_and_bounds_fail(self):
        for key,value in [('sequence',True),('schema',True),('wait_frame_end_xr_ns',43),
                          ('render_entry_xr_ns',float('nan')),('history',[]),('alpha_required',True),
                          ('selected',[]),('repeat_previous',True)]:
            r,p=fixture();r[key]=value
            self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        self.assertEqual(module.evaluate([],[])['status'],'fail')
        self.assertEqual(module.evaluate([{}]*4097,[{}])['status'],'fail')

    def test_failed_write_missing_counter_or_duplicate_trace_fails(self):
        for key,value in [('candidate_trace_write_ok',False),('candidate_trace_records',True),
                          ('candidate_trace_dropped',1),('candidate_trace_write_ns',float('nan'))]:
            r,p=fixture();p[key]=value
            self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        r,p=fixture();self.assertEqual(module.evaluate([r,r],[p,p])['status'],'fail')

    def test_retained_fallback_requires_observed_previous_pair(self):
        r,p=fixture();r2,p2=copy.deepcopy(r),copy.deepcopy(p)
        r2.update(sequence=2,reason='no_complete_fallback',repeat_previous=True)
        r2['history']=[dict(decoder=i//3,slot=i%3,present=False) for i in range(9)]
        for key in ('wait_frame_begin_xr_ns','wait_frame_end_xr_ns','render_entry_xr_ns',
                    'selection_begin_xr_ns','selection_end_xr_ns','end_frame_return_xr_ns'):
            r2[key]+=100
            if key in p2:p2[key]+=100
        r2['predicted_display_xr_ns'] = p2['predicted_display_xr_ns'] = 200
        p2.update(candidate_trace_records=2,submit_xr_ns=165,render_fence_begin_xr_ns=152,render_fence_end_xr_ns=153)
        self.assertEqual(module.evaluate([r,r2],[p,p2])['status'],'pass')
        r2['selected'][0]['frame']=3
        self.assertEqual(module.evaluate([r,r2],[p,p2])['status'],'fail')

    def test_disabled_must_be_absent(self):
        self.assertEqual(module.evaluate([],[],False)['status'],'off')
        r,p=fixture();self.assertEqual(module.evaluate([r],[p],False)['status'],'fail')

    def test_alpha_participation_cannot_be_hidden(self):
        r,p=fixture()
        for f in r['history']+r['selected']:
            if f['present']:f['alpha']=True
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')

    def test_fence_time_order_is_checked_for_native_diagnostics_too(self):
        r,p=fixture();p['render_fence_begin_xr_ns']=999;p['render_fence_end_xr_ns']=1
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        r,p=fixture();r['selection_phase']=p['selection_phase']='after-fence'
        self.assertEqual(module.evaluate([r],[p])['status'],'fail')
        p.update(render_fence_begin_xr_ns=41,render_fence_end_xr_ns=42)
        self.assertEqual(module.evaluate([r],[p])['status'],'pass')

    def test_same_identity_cannot_change_target_or_publication(self):
        r,p=fixture();r2,p2=copy.deepcopy(r),copy.deepcopy(p)
        r2['sequence']=2;r2['repeat_previous']=True
        for k in ('wait_frame_begin_xr_ns','wait_frame_end_xr_ns','render_entry_xr_ns',
                  'selection_begin_xr_ns','selection_end_xr_ns','end_frame_return_xr_ns','predicted_display_xr_ns'):
            r2[k]+=100
            if k in p2:p2[k]+=100
        p2.update(candidate_trace_records=2,submit_xr_ns=165,render_fence_begin_xr_ns=152,render_fence_end_xr_ns=153)
        r2['reason']='no_complete_fallback'
        r2['history']=[dict(decoder=i//3,slot=i%3,present=False) for i in range(9)]
        for f in r2['selected'][:2]:f['view_display_time_xr_ns']+=1
        self.assertEqual(module.evaluate([r,r2],[p,p2])['status'],'fail')

    def test_entire_record_cannot_regress_together(self):
        r,p=fixture();r2,p2=copy.deepcopy(r),copy.deepcopy(p)
        r2['sequence']=2;r2['repeat_previous']=True;p2['candidate_trace_records']=2
        for k in list(r2):
            if k.endswith('_xr_ns'):
                r2[k]-=5
                if k in p2:p2[k]-=5
        p2['submit_xr_ns']-=5;p2['render_fence_begin_xr_ns']-=5;p2['render_fence_end_xr_ns']-=5
        self.assertEqual(module.evaluate([r,r2],[p,p2])['status'],'fail')

    def test_budget_boundary_records_cap_and_drops_are_explicit(self):
        r,p=fixture();r['history'][1]=dict(decoder=0,slot=1,present=False)
        r['history'][4]=dict(decoder=1,slot=1,present=False)
        traces=[];presents=[]
        for i in range(4097):
            rr=dict(r,sequence=i+1,repeat_previous=bool(i));pp=dict(p)
            for k in rr:
                if k.endswith('_xr_ns'):
                    rr[k]+=100*i
                    if k in pp:pp[k]+=100*i
            pp.update(submit_xr_ns=65+100*i,render_fence_begin_xr_ns=52+100*i,
                      render_fence_end_xr_ns=53+100*i,candidate_trace_records=min(i+1,4096),
                      candidate_trace_dropped=max(0,i+1-4096),candidate_trace_write_ns=10000 if i<4096 else 0)
            if i<4096:traces.append(rr)
            presents.append(pp)
        self.assertEqual(module.evaluate(traces,presents)['status'],'pass')
        presents[-1]['candidate_trace_write_ns']=1
        self.assertEqual(module.evaluate(traces,presents)['status'],'fail')

    def test_input_types_and_disabled_partial_trace_fail(self):
        self.assertEqual(module.evaluate(None,[])['status'],'fail')
        self.assertEqual(module.evaluate([], [None])['status'],'fail')
        self.assertEqual(module.evaluate([], [dict(candidate_trace_write_ns=0)],False)['status'],'fail')

    def test_schema2_alpha_fallback_does_not_invent_a_complete_candidate(self):
        r,p=bounded_fixture();r2,p2=copy.deepcopy(r),copy.deepcopy(p)
        r2.update(sequence=2,reason='no_complete_fallback',repeat_previous=True,
                  alpha_required=True,selected_alpha_required=False,
                  previous=copy.deepcopy(r['selected']),
                  closest=[dict(decoder=i,present=False) for i in range(3)],
                  chosen_slots=[-1]*3,eligible_left_slots=[False]*3)
        r2['history']=[dict(decoder=i//3,slot=i%3,present=False) for i in range(9)]
        # The new alpha-bearing left/right pair lacks its required third handle.
        # The retained selection legitimately has no alpha participation.
        for eye in range(2):
            r2['history'][eye*3]=dict(decoder=eye,slot=0,present=True,frame=3,warp=13,
                view_display_time_xr_ns=210,receive_xr_ns=10,ready_xr_ns=20,alpha=True)
        for key in ('wait_frame_begin_xr_ns','wait_frame_end_xr_ns','render_entry_xr_ns',
                    'selection_begin_xr_ns','selection_end_xr_ns','end_frame_return_xr_ns',
                    'predicted_display_xr_ns'):
            r2[key]+=100
            if key in p2:p2[key]+=100
        r2['previous_predicted_display_xr_ns']=p2['previous_predicted_display_xr_ns']=100
        p2.update(candidate_trace_records=2,submit_xr_ns=165,
                  render_fence_begin_xr_ns=152,render_fence_end_xr_ns=153)
        result=module.evaluate([r,r2],[p,p2],selection_policy='bounded-newest')
        self.assertEqual(result['status'],'pass',result['errors'])
        self.assertEqual(result['fallback_records'],1)
        self.assertEqual(result['higher_frame_id_available'],0)
        self.assertEqual(result['selected_differs_from_newest_complete'],0)

    def test_schema2_requested_policy_is_bound_after_trace_cap(self):
        r,p=bounded_fixture()
        r.update(selection_policy='closest-target',reason='closest_target',
                 selected=copy.deepcopy(r['closest']),chosen_slots=[0,0,-1],
                 eligible_left_slots=[True,False,False])
        p.update(selection_policy='closest-target',left=1,right=1,left_warp=11,right_warp=11)
        for pos in (1,4):r['history'][pos]=dict(decoder=pos//3,slot=1,present=False)
        traces=[];presents=[]
        for i in range(4097):
            rr,pp=copy.deepcopy(r),dict(p)
            rr.update(sequence=i+1,repeat_previous=bool(i))
            if i:rr['previous']=copy.deepcopy(r['selected'])
            for key in ('wait_frame_begin_xr_ns','wait_frame_end_xr_ns','render_entry_xr_ns',
                        'selection_begin_xr_ns','selection_end_xr_ns','end_frame_return_xr_ns',
                        'predicted_display_xr_ns'):
                rr[key]+=100*i
                if key in pp:pp[key]+=100*i
            if i:rr['previous_predicted_display_xr_ns']=pp['previous_predicted_display_xr_ns']=100*i
            pp.update(submit_xr_ns=65+100*i,render_fence_begin_xr_ns=52+100*i,
                      render_fence_end_xr_ns=53+100*i,candidate_trace_records=min(i+1,4096),
                      candidate_trace_dropped=max(0,i+1-4096),
                      candidate_trace_write_ns=10000 if i<4096 else 0)
            if i<4096:traces.append(rr)
            presents.append(pp)
        result=module.evaluate(traces,presents,selection_policy='closest-target')
        self.assertEqual(result['status'],'pass',result['errors'])
        presents[-1]['selection_policy']='bounded-newest'
        for requested in ('closest-target',None):
            result=module.evaluate(traces,presents,selection_policy=requested)
            self.assertEqual(result['status'],'fail')
            self.assertIn('presentation selection policy differs within diagnostic run',result['errors'])
