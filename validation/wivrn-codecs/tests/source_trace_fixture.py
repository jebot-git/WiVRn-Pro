"""Shared exact host source fixture, extracted from be5fd0bd counterfactual tests.

Only source events are needed by native pacer/publication acquisition tests.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('native_source_fixture', ROOT/'prototypes/vr444/experiments/compositor_freshness/analyze.py')
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def sources(same_app=False, clock_offset=0, frames=(1, 2)):
    def event(name, **values):
        r = dict.fromkeys(source.INTS, 0)
        r.update(dict.fromkeys(source.BOOLS, False))
        r.update(type='event', event=name, app_frame_id=-1, system_frame_id=-1,
                 when_ns=1_000_000, observed_ns=1_000_001)
        r.update(values)
        for key in ('when_ns', 'observed_ns', 'display_time_ns', 'wake_up_ns', 'layer_target_ns'):
            if r[key]:
                r[key] += clock_offset
        return r
    records = []
    for app_id in (1,) if same_app else frames:
        app = dict(pacer_id=1, app_frame_id=app_id)
        records += [event('app_predict', **app, display_time_ns=10_000_000, display_period_ns=11_111_111, wake_up_ns=2_000_000),
                    event('app_delivered', **app, when_ns=3_000_000, observed_ns=3_000_001, display_time_ns=10_000_000),
                    event('app_gpu_done', **app, when_ns=4_000_000, observed_ns=4_000_001)]
    for frame in frames:
        records += [event('app_latched', pacer_id=1, app_frame_id=1 if same_app else frame, system_frame_id=frame,
                          when_ns=5_000_000, observed_ns=5_000_001),
                    event('compositor_frame', system_frame_id=frame, when_ns=6_000_000, observed_ns=6_000_000,
                          display_time_ns=11_000_000, display_period_ns=11_111_111, layer_target_ns=10_000_000,
                          layer_count=1, projection_count=1, direct_projection=True, projection_pose_reused=True),
                    event('projection_layer', system_frame_id=frame, when_ns=6_000_000, observed_ns=6_000_000,
                          layer_target_ns=10_000_000)]
    for i, row in enumerate(records, 1):
        row['sequence'] = i
    return [dict(type='header', schema=1, clock='host_monotonic_ns', capacity=131072, diagnostic_only=True),
            *records, dict(type='footer', records=len(records), dropped=0, complete=True)]

