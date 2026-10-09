#!/usr/bin/env python3
"""Strict replay of diagnostic host pacer snapshots, without a latency claim."""
import argparse
import collections
import json
from pathlib import Path

FIELDS = {'sequence', 'pacer_id', 'frame_id', 'now_ns', 'previous_client_render_ns',
          'period_ns', 'wake_to_present_ns', 'safe_decode_ns', 'render_to_display_ns',
          'margin_ns', 'slop_ns', 'client_render_ns', 'desired_present_ns', 'wake_up_ns', 'display_ns'}
I64_MAX = (1 << 63) - 1


def distribution(values):
    values = sorted(values)
    def quantile(q):
        position = (len(values) - 1) * q
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return (values[lo] + (values[hi] - values[lo]) * (position - lo)) / 1e6
    return {k: quantile(q) for k, q in (('p50', .5), ('p95', .95), ('p99', .99))} | {
        'count': len(values), 'min': values[0] / 1e6, 'max': values[-1] / 1e6}


def evaluate(rows):
    result = dict(status='fail', diagnostic_only=True, promotion_blocked=True, errors=[], limitations=[
        'Predictor inputs are estimates and clock-converted feedback history, not measured future deadlines.',
        'No client clock subtraction, input freshness or photon latency is inferred.',
        'The trace records predictions through recorder close, not process-tail completion.',
        'A repeated frame identity across pacer instances must not be joined by frame ID alone.',
        'Feedback estimator samples, conversion uncertainty and actual wake/submission are not recorded.',
        'Diagnostics add work inside the pacer mutex; this trace cannot qualify performance.'])
    errors = result['errors']
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    def checked(value):
        require(-(1 << 63) <= value <= I64_MAX, 'Native signed arithmetic would overflow.')
        return value
    try:
        require(isinstance(rows, list) and len(rows) >= 3 and all(isinstance(r, dict) for r in rows),
                'Expected header, nonempty predictions, and footer objects.')
        header, footer = rows[0], rows[-1]
        require(set(header) == {'type', 'schema', 'clock', 'capacity', 'diagnostic_only', 'coverage'}, 'Header keys invalid.')
        require(header['type'] == 'header' and type(header['schema']) is int and header['schema'] == 1,
                'Header schema invalid.')
        require(header['clock'] == 'host_monotonic_ns' and header['diagnostic_only'] is True and
                header['coverage'] == 'predict_calls_until_close', 'Header semantics invalid.')
        require(type(header['capacity']) is int and 0 < header['capacity'] <= 65536, 'Capacity invalid.')
        require(set(footer) == {'type', 'records', 'dropped', 'complete'}, 'Footer keys invalid.')
        require(footer['type'] == 'footer' and footer['complete'] is True and
                type(footer['records']) is int and footer['records'] == len(rows) - 2 and
                type(footer['dropped']) is int and footer['dropped'] == 0, 'Missing, partial, or dropped capture.')
        require(footer['records'] <= header['capacity'], 'Record count exceeds capacity.')
        previous = {}
        residuals, periods_skipped, wake_leads = [], [], []
        budgets = collections.defaultdict(list)
        for index, r in enumerate(rows[1:-1], 1):
            require(set(r) == FIELDS | {'type'} and r['type'] == 'prediction', f'Prediction {index}: keys/type invalid.')
            require(all(type(r[k]) is int and -(1 << 63) <= r[k] <= I64_MAX for k in FIELDS),
                    f'Prediction {index}: expected signed int64 values.')
            require(r['sequence'] == index and r['pacer_id'] > 0 and r['frame_id'] >= 0,
                    f'Prediction {index}: sequence/identity invalid.')
            require(r['now_ns'] > 0 and r['period_ns'] > 0 and r['previous_client_render_ns'] >= 0,
                    f'Prediction {index}: clock/period invalid.')
            require(all(r[k] >= 0 for k in ('wake_to_present_ns', 'safe_decode_ns', 'render_to_display_ns')),
                    f'Prediction {index}: negative timing budget.')
            require(r['margin_ns'] == 3_000_000 and r['slop_ns'] == 500_000,
                    f'Prediction {index}: unexpected native margin/slop contract.')
            prior = previous.get(r['pacer_id'])
            require(r['frame_id'] == (prior['frame_id'] + 1 if prior else 0),
                    f'Prediction {index}: missing/duplicate predictor call.')
            require(r['now_ns'] >= prior['now_ns'] if prior else r['previous_client_render_ns'] == 0,
                    f'Prediction {index}: nonmonotonic clock or noninitial first prediction.')
            target = checked(r['previous_client_render_ns'] + r['period_ns'])
            budget_target = checked(checked(r['now_ns'] + r['wake_to_present_ns']) + r['safe_decode_ns'])
            steps = 0
            if budget_target > target:
                steps = checked(budget_target - target) // r['period_ns']
                target = checked(target + checked(r['period_ns'] * steps))
            desired = checked(target - r['safe_decode_ns'])
            wake = checked(checked(desired - r['wake_to_present_ns']) + r['margin_ns'])
            display = checked(target + r['render_to_display_ns'])
            require((r['client_render_ns'], r['desired_present_ns'], r['wake_up_ns'], r['display_ns']) ==
                    (target, desired, wake, display), f'Prediction {index}: native predictor replay differs.')
            require(min(target, desired, wake, display) > 0, f'Prediction {index}: nonpositive predicted timestamp.')
            residuals.append(target - budget_target)
            periods_skipped.append(steps)
            wake_leads.append(wake - r['now_ns'])
            for key in ('wake_to_present_ns', 'safe_decode_ns', 'render_to_display_ns'):
                budgets[key].append(r[key])
            previous[r['pacer_id']] = r
        result.update(status='pass', records=footer['records'], pacer_instances=len(previous),
                      budget_ms={k: distribution(v) for k, v in budgets.items()},
                      predicted_wake_lead_ms=distribution(wake_leads),
                      client_render_minus_budget_target_ms=distribution(residuals),
                      floor_period_advance_count=sum(steps > 0 for steps in periods_skipped),
                      max_periods_advanced=max(periods_skipped),
                      predicted_wake_before_now_count=sum(v < 0 for v in wake_leads))
    except (KeyError, ValueError) as exc:
        errors.append(str(exc))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    try:
        result = evaluate([json.loads(line) for line in args.trace.read_text().splitlines()])
    except (ValueError, OSError) as exc:
        result = dict(status='fail', diagnostic_only=True, promotion_blocked=True, errors=[str(exc)])
    output = json.dumps(result, indent=2) + '\n'
    if args.out:
        with args.out.open('x') as stream:
            stream.write(output)
    else:
        print(output, end='')
    return result['status'] != 'pass'


if __name__ == '__main__':
    raise SystemExit(main())
