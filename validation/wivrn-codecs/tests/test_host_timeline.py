import copy
import importlib.util
from pathlib import Path
import unittest
from test_candidate_trace import fixture as selected_fixture
from test_selection_counterfactual import sources

spec=importlib.util.spec_from_file_location('host_timeline_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/host_timeline/analyze.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def resequence(rows):
    for i,row in enumerate(rows[1:-1],1):row['sequence']=i
    rows[-1]['records']=len(rows)-2


def fixture(two_frames=False,same_app=False):
    trace,present=selected_fixture()
    traces,presented=[trace],[present]
    source=sources(frames=(1,2) if two_frames else (1,),same_app=same_app)
    points=[]
    for row in source[1:-1]:
        if row['event']=='app_predict':
            for point,when in ((0,2_000_000),(1,2_500_000)):
                event=copy.deepcopy(row)
                event.update(event='app_point',timing_point=point,when_ns=when,observed_ns=when+1,
                    display_time_ns=0,display_period_ns=0,wake_up_ns=0)
                points.append(event)
    source[-1:-1]=points;resequence(source)
    if two_frames:
        row,p=copy.deepcopy(trace),copy.deepcopy(present)
        row['sequence']=2
        for obj in (row,p):
            for key in obj:
                if key.endswith('_xr_ns') and key not in ('left_receive_xr_ns','right_receive_xr_ns','left_decode_ready_xr_ns','right_decode_ready_xr_ns'):
                    obj[key]+=100
        row['selected']=[{k:v for k,v in row['history'][i].items() if k!='slot'} for i in (1,4)]+[dict(decoder=2,present=False)]
        p.update(left=2,right=2,left_warp=12,right_warp=12,candidate_trace_records=2)
        traces.append(row);presented.append(p)
    return traces,presented,source


class HostTimelineTests(unittest.TestCase):
    def test_exact_callback_stages_and_distinct_prediction_lead(self):
        r=module.evaluate(*fixture(),warmup_ns=0)
        self.assertEqual(r['warm_first_encoded']['count'],1)
        sample=r['samples'][0]
        self.assertEqual(list(sample['intervals_ns'].values()),[500_000,500_000,1_000_000,1_000_000,1_000_000])
        self.assertEqual(sum(sample['intervals_ns'].values()),sample['wake_to_accepted_input_ns'])
        self.assertEqual(sample['wake_to_accepted_input_ns'],4_000_000)
        self.assertEqual(sample['phase_ns']['compositor_prediction_minus_accepted_input_ns'],5_000_000)
        self.assertEqual(sample['phase_ns']['source_layer_target_minus_accepted_input_ns'],4_000_000)
        self.assertFalse(r['cross_host_client_subtraction'])

    def test_actual_warm_source_history_survives_new_encoded_id(self):
        traces,presented,source=fixture(two_frames=True,same_app=True)
        all_rows=module.evaluate(traces,presented,source,warmup_ns=0)
        self.assertEqual(all_rows['warm_first_encoded']['count'],2)
        self.assertEqual(all_rows['warm_first_source_generation']['count'],1)
        self.assertEqual(all_rows['warm_reused_source_generation']['count'],1)
        self.assertEqual(all_rows['warm_repeated_host_latch']['count'],1)
        warm=module.evaluate(traces,presented,source,warmup_ns=100)
        self.assertEqual(warm['warm_first_encoded']['count'],1)
        self.assertEqual(warm['warm_first_source_generation']['count'],0)
        self.assertEqual(warm['warm_reused_source_generation']['count'],1)

    def test_unselected_prior_host_latch_is_distinct_from_selection_history(self):
        traces,presented,source=fixture(two_frames=True,same_app=True)
        # Keep only the second selected frame but retain complete host evidence.
        traces,presented=traces[1:],presented[1:]
        traces[0]['sequence']=1;presented[0]['candidate_trace_records']=1
        r=module.evaluate(traces,presented,source,warmup_ns=0)
        self.assertEqual(r['warm_first_source_generation']['count'],1)
        self.assertEqual(r['warm_reused_source_generation']['count'],0)
        self.assertEqual(r['warm_first_host_latch']['count'],0)
        self.assertEqual(r['warm_repeated_host_latch']['count'],1)
        self.assertEqual(r['samples'][0]['prior_host_latches_of_source'],1)

    def test_host_clock_translation_preserves_every_duration_and_phase(self):
        traces,presented,source=fixture();before=module.evaluate(traces,presented,source,warmup_ns=0)
        for row in source[1:-1]:
            for key in ('when_ns','observed_ns','display_time_ns','wake_up_ns','layer_target_ns'):
                if row[key]:row[key]+=10**12
        after=module.evaluate(traces,presented,source,warmup_ns=0)
        for key in ('samples','warm_first_encoded','warm_first_source_generation'):
            self.assertEqual(before[key],after[key])

    def test_independent_client_clock_translation_cannot_change_host_intervals(self):
        traces,presented,source=fixture();before=module.evaluate(traces,presented,source,warmup_ns=0)
        for row in traces+presented:
            for key in row:
                if key.endswith('_xr_ns'):row[key]+=10**13
        for frame in traces[0]['history']+traces[0]['selected']:
            if frame.get('present'):
                for key in ('view_display_time_xr_ns','receive_xr_ns','ready_xr_ns'):frame[key]+=10**13
        after=module.evaluate(traces,presented,source,warmup_ns=0)
        self.assertEqual(before['samples'],after['samples'])

    def test_missing_duplicate_regressive_or_unsupported_source_fails(self):
        for mode in range(6):
            traces,presented,source=fixture()
            wake=next(r for r in source if r.get('event')=='app_point' and r['timing_point']==0)
            begin=next(r for r in source if r.get('event')=='app_point' and r['timing_point']==1)
            if mode==0:source.remove(begin)
            elif mode==1:source.insert(-1,copy.deepcopy(wake))
            elif mode==2:begin.update(when_ns=3_500_000,observed_ns=3_500_001)
            elif mode==3:source[0]['clock']='client_xr_ns'
            elif mode==4:
                frame=next(r for r in source if r.get('event')=='compositor_frame')
                frame.update(layer_count=2,direct_projection=False)
            else:wake['timing_point']=False
            resequence(source)
            with self.subTest(mode=mode),self.assertRaises(ValueError):module.evaluate(traces,presented,source,warmup_ns=0)

    def test_early_wake_is_a_signed_phase_not_negative_stage(self):
        traces,presented,source=fixture()
        next(r for r in source if r.get('event')=='app_predict')['wake_up_ns']=2_000_001
        r=module.evaluate(traces,presented,source,warmup_ns=0)
        self.assertEqual(r['samples'][0]['phase_ns']['actual_wake_minus_requested_wake_ns'],-1)
        for warm in (True,-1):
            with self.assertRaises(ValueError):module.evaluate(traces,presented,source,warmup_ns=warm)
