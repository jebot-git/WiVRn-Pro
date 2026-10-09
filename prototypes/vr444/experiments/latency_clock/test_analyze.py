import copy
import unittest
from analyze import analyze, offset_bracket


def fixture():
    p=dict(left=4,right=4,left_warp=9,right_warp=9,submit_xr_ns=100_000_000,
        predicted_display_xr_ns=120_000_000,left_receive_xr_ns=75_000_000,
        right_receive_xr_ns=76_000_000,left_decode_ready_xr_ns=80_000_000,
        right_decode_ready_xr_ns=81_000_000,selection_begin_xr_ns=85_000_000,
        selection_end_xr_ns=86_000_000,end_frame_return_xr_ns=99_000_000,
        left_repeat_count=1,right_repeat_count=1)
    f=dict(frame_index=4,encode_ms=2,encode_to_send_ms=1,delivery_ms=3,
           decoder_queue_ms=0,decode_ms=2,decoded_to_blit_ms=12,
           blit_to_predicted_display_ms=30,encode_to_predicted_display_ms=50)
    s=dict(performance=dict(session=7,streams=[dict(id=0,first_display=f)]))
    return p,s


class ClockAnalysisTests(unittest.TestCase):
    def test_offset_symmetry_and_asymmetry_bracket_not_confidence(self):
        self.assertEqual(offset_bracket(100,170,140),dict(offset_min_ns=30,offset_max_ns=70,midpoint_offset_ns=50,half_rtt_ns=20))
        # True b=50, outbound delay=30, return delay=10: midpoint is biased +10.
        a=offset_bracket(100,180,140)
        self.assertEqual(a['midpoint_offset_ns'],60)
        self.assertTrue(a['offset_min_ns']<=50<=a['offset_max_ns'])
        for args in ((True,2,3),(0,2,3),(100,150,99)):
            with self.assertRaises(ValueError):offset_bracket(*args)

    def test_same_frame_join_nominal_only(self):
        p,s=fixture();r=analyze([p],[s],0)
        self.assertEqual(r['local_ms']['predicted_target_minus_submit_ms']['p50'],20)
        self.assertEqual(r['sampled_feedback']['distributions_ms']['nominal_converted_encode_to_submit_ms']['p50'],30)
        self.assertFalse(r['clock_error_bound_available'])
        self.assertFalse(r['source_epoch_available'])

    def test_dedup_and_mismatching_frame_not_inferred(self):
        p,s=fixture();r=analyze([p],[s,s],0)
        self.assertEqual(r['sampled_feedback']['joined_eyes'],1)
        self.assertEqual(r['sampled_feedback']['duplicate_snapshots'],1)
        s['performance']['streams'][0]['first_display']['frame_index']=5
        self.assertEqual(analyze([p],[s],0)['sampled_feedback']['joined_eyes'],0)

    def test_failed_first_submission_repeat_not_assumed_same_target(self):
        p,s=fixture();p['left_repeat_count']=2
        r=analyze([p],[s],0)
        self.assertEqual(r['sampled_feedback']['joined_eyes'],0)
        self.assertEqual(r['sampled_feedback']['excluded_first_success_was_repeat'],1)

    def test_malformed_identity_timing_and_feedback_rejected(self):
        for key,value in [('right',6),('left_warp',True),('left_receive_xr_ns',90_000_000)]:
            p,s=fixture();p[key]=value
            with self.assertRaises(ValueError):analyze([p],[s],0)
        for key,value in [('encode_ms',float('nan')),('encode_ms',True),('encode_ms',3)]:
            p,s=fixture();s['performance']['streams'][0]['first_display'][key]=value
            with self.assertRaises(ValueError):analyze([p],[s],0)

    def test_duplicate_changes_and_multiple_sessions_rejected(self):
        p,s=fixture();other=copy.deepcopy(s);other['performance']['session']=8
        with self.assertRaises(ValueError):analyze([p],[s,other],0)
        other=copy.deepcopy(s);f=other['performance']['streams'][0]['first_display']
        f['encode_ms']+=1;f['encode_to_predicted_display_ms']+=1
        with self.assertRaises(ValueError):analyze([p],[s,other],0)


if __name__=='__main__':unittest.main()
