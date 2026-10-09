"""Score one selected-candidate sub-15 ms run; no full-goal or release claim."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('research_latency_policy', HERE/'latency_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def evaluate(rows, provenance):
    target = json.loads((HERE/'RESEARCH-TARGETS.json').read_text())['latency']
    result = policy.evaluate(rows, provenance)
    errors = list(result['errors'])
    if not isinstance(provenance, dict):
        provenance = {}
    if provenance.get('mode') not in ('custom-vr444', 'custom-rrb32'):
        errors.append('selected custom candidate provenance required')
    p95 = result['observed']['stereo_receive_to_submit_ms_p95']
    if p95 is None or p95 >= target['strict_p95_upper_bound_ms']:
        errors.append('complete stereo receive-to-submit p95 must be strictly below 15 ms')
    return result | dict(status='fail' if errors else 'pass', errors=errors,
        target_id='selected-candidates-sub15-single-run-v1', target=target,
        goal_verified=False, evidence_integrity_verified=False,
        promotion_eligible=False, release_ready=False,
        scope='Single-run software score only. Independent raw correctness, current build identity, complete repeated runs and freshness validation remain required for research qualification; no quality or photon claim.')
