# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from pathlib import Path
from dataclasses import replace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'dashboard/shora'))
from local_speaker import SpeakerTracker
from local_backend import LocalBackend
from subtitle_backend import Settings, Voices, Segment


def vector(index):return [float(i==index) for i in range(256)]


class SpeakerTests(unittest.TestCase):
    def test_repeated_different_and_capacity(self):
        tracker=SpeakerTracker()
        self.assertEqual([tracker.assign(vector(i)) for i in (0,1,0,2,3,4,1)],[1,2,1,3,4,0,2])

    def test_invalid_or_uncertain_is_neutral(self):
        tracker=SpeakerTracker()
        for value in (None,[],[0]*256,[float('nan')]*256):self.assertEqual(tracker.assign(value),0)
        tracker.assign(vector(0));tracker.assign(vector(1))
        self.assertEqual(tracker.assign([2**-.5,2**-.5]+[0]*254),0)

    def test_local_colours_preserve_text_times_and_reset(self):
        backend=LocalBackend();voices=Voices()
        settings=replace(Settings(),recognition_backend='openvino',language='Original')
        result=dict(text='日本語 한국어',segments=[dict(text='日本語',offset=0,embedding=vector(0)),
            dict(text='한국어',offset=2,embedding=vector(1))])
        segment=Segment(b'\0'*160000,0,[.5]*250)
        with patch.object(backend,'infer',return_value=result):
            lines=backend.subtitle(segment,settings,voices)
            self.assertEqual([l['speaker'] for l in lines],[1,2])
            self.assertEqual([l['offset'] for l in lines],[0,2])
            self.assertTrue(all('embedding' not in l for l in lines))
            self.assertEqual(backend.subtitle(segment,settings,voices)[0]['speaker'],1)
            self.assertEqual(backend.subtitle(segment,replace(settings,distinguish_voices=False),voices)[0]['speaker'],0)
        self.assertFalse(hasattr(Voices(),'local_tracker'))

if __name__=='__main__':unittest.main()
