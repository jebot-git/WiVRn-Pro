"""Score the preliminary CPU RGB444 sub-15ms target without weakening the gate."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cpu_target_latency_policy', HERE/'latency_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def evaluate(rows, provenance):
    target = json.loads((HERE/'CPU-LATENCY-TARGET.json').read_text())
    result = policy.evaluate(rows, provenance)
    errors = list(result['errors'])
    if not isinstance(provenance, dict) or provenance.get('mode') != 'custom-vr444' or provenance.get('decoder_backend') != 'cpu-neon':
        errors.append('preliminary CPU target requires custom VR444 CPU NEON provenance')
    p95 = result['observed']['stereo_receive_to_submit_ms_p95']
    if p95 is None or p95 >= target['strict_upper_bound_ms']:
        errors.append('stereo receive-to-submit p95 must be strictly below 15 ms')
    return result | dict(status='fail' if errors else 'pass', errors=errors,
                         target_id=target['target_id'], target=target,
                         promotion_eligible=False, release_ready=False,
                         color_quality_verified=False,
                         scope='Preliminary software latency score only; actual CPU path, color correctness, repeats and freshness require separate evidence')
