#!/usr/bin/env python3
"""Strict publication-prefix reconstruction; no complete-tail or latency claim."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('publication_candidate_trace', HERE / 'candidate_trace.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
LIMIT = 16384
COVERAGE = 'scene_start_through_last_selection_checkpoint'
FIELDS = {'present', 'frame', 'warp', 'view_display_time_xr_ns', 'receive_xr_ns', 'ready_xr_ns', 'alpha'}


def integer(value, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('invalid bounded integer')
    return value


def keys(row, expected):
    if type(row) is not dict or set(row) != set(expected):
        raise ValueError('unexpected publication record fields')


def frame(row):
    if type(row) is not dict or type(row.get('present')) is not bool:
        raise ValueError('invalid frame presence')
    if not row['present']:
        keys(row, {'present'})
        return row
    keys(row, FIELDS)
    integer(row['frame'])
    integer(row['warp'], maximum=2**64-1)
    for key in ('view_display_time_xr_ns', 'receive_xr_ns', 'ready_xr_ns'):
        integer(row[key], 1)
    if type(row['alpha']) is not bool or row['ready_xr_ns'] < row['receive_xr_ns']:
        raise ValueError('invalid frame timestamp or alpha')
    return row


def history(rows):
    if type(rows) is not list or len(rows) != 9:
        raise ValueError('publication checkpoint requires nine history slots')
    result = []
    for index, row in enumerate(rows):
        if type(row) is not dict or integer(row.get('decoder'), 0, 2) != index // 3 or integer(row.get('slot'), 0, 2) != index % 3:
            raise ValueError('history slots are not in canonical decoder order')
        result.append(frame({k: v for k, v in row.items() if k not in ('decoder', 'slot')}))
    return result


def replay(rows, traces):
    """Reconstruct only the prefix; caller separately validates presentation."""
    if type(rows) is not list or not 2 <= len(rows) <= LIMIT + 4097 or type(traces) is not list or not 1 <= len(traces) <= 4096:
        raise ValueError('missing or oversized publication/candidate population')
    keys(rows[0], {'kind', 'schema', 'capacity', 'clock', 'coverage'})
    if (rows[0]['kind'] != 'header' or integer(rows[0]['schema'], 1, 1) != 1 or
            rows[0]['clock'] != 'client_xr_ns' or rows[0]['coverage'] != COVERAGE):
        raise ValueError('unsupported publication header')
    capacity = integer(rows[0]['capacity'], 1, LIMIT)
    if type(rows[-1]) is not dict or rows[-1].get('kind') != 'checkpoint':
        raise ValueError('publication export has an unbound trailing event')
    slots = [dict(present=False) for _ in range(9)]
    events = checkpoints = epoch = resets = publishes = last_ready = 0
    metadata, observed = {}, set()
    for row in rows[1:]:
        if type(row) is not dict or row.get('kind') not in ('reset', 'publish', 'checkpoint'):
            raise ValueError('unknown publication event')
        kind = row['kind']
        if kind in ('reset', 'publish'):
            expected = {'kind', 'sequence', 'epoch'}
            if kind == 'publish':
                expected |= {'decoder', 'added', 'evicted'}
            keys(row, expected)
            events += 1
            if events > capacity or integer(row['sequence'], 1) != events:
                raise ValueError('publication sequence missing, duplicated or over capacity')
            if kind == 'reset':
                epoch += 1
                if integer(row['epoch'], 1) != epoch:
                    raise ValueError('publication reset epoch is not consecutive')
                slots = [dict(present=False) for _ in range(9)]
                resets += 1
                continue
            if not epoch or integer(row['epoch'], 1) != epoch:
                raise ValueError('publication outside current decoder generation')
            decoder = integer(row['decoder'], 0, 2)
            added, evicted = frame(row['added']), frame(row['evicted'])
            if not added['present'] or evicted != slots[decoder * 3]:
                raise ValueError('publication eviction does not match reconstructed history')
            if added['ready_xr_ns'] < last_ready:
                raise ValueError('publication ready clock regresses under shared lock')
            last_ready = added['ready_xr_ns']
            identity = (epoch, decoder, added['frame'])
            if identity in metadata and metadata[identity] != added:
                raise ValueError('published frame metadata changed within decoder generation')
            metadata[identity] = added
            slots[decoder * 3:decoder * 3 + 3] = slots[decoder * 3 + 1:decoder * 3 + 3] + [added]
            publishes += 1
            continue
        keys(row, {'kind', 'selection_sequence', 'prefix_records', 'epoch', 'dropped', 'valid', 'history'})
        checkpoints += 1
        if (integer(row['selection_sequence'], 1) != checkpoints or checkpoints > len(traces) or
                integer(row['prefix_records']) != events or integer(row['epoch']) != epoch or
                integer(row['dropped']) != 0 or row['valid'] is not True):
            raise ValueError('publication checkpoint reports lost, invalid or mismatched prefix')
        trace = traces[checkpoints - 1]
        if type(trace) is not dict or integer(trace.get('sequence'), 1) != checkpoints:
            raise ValueError('candidate sequence differs from publication checkpoint')
        if history(row['history']) != slots or row['history'] != trace.get('history'):
            raise ValueError('reconstructed publication history differs from candidate snapshot')
        if last_ready > integer(trace.get('selection_end_xr_ns'), 1):
            raise ValueError('publication occurred after referenced selection')
        for index, item in enumerate(slots):
            if item['present']:
                observed.add((epoch, index // 3, item['frame']))
    if checkpoints != len(traces):
        raise ValueError('candidate selections lack publication checkpoints')
    return dict(events=events, reset_events=resets, publication_events=publishes, checkpoints=checkpoints,
        last_decoder_epoch=epoch, distinct_published_eye_identities=len(metadata),
        published_eye_identities_absent_all_candidate_snapshots=len(set(metadata) - observed))


def evaluate(rows, traces, presented, enabled=True):
    result = dict(diagnostic_only=True, promotion_eligible=False, release_ready=False,
        latency_gate_evaluated=False, complete_process_tail=False,
        coverage=COVERAGE, terminal_publication_tail='not_recorded',
        timestamp_scope='Existing ready timestamps precede frames-mutex release; checkpoint order proves prefix visibility, not an exact alternative selection instant.')
    if type(enabled) is not bool or any(type(value) is not list for value in (rows, traces, presented)):
        return result | dict(status='fail', errors=['invalid publication evaluation inputs'])
    if not enabled:
        return result | dict(status='fail' if rows else 'off', errors=['publication trace present while disabled'] if rows else [])
    verified = candidate.evaluate(traces, presented, enabled=True)
    if verified['status'] != 'pass':
        return result | dict(status='fail', errors=['candidate/presentation validation failed'], candidate_validation=verified)
    try:
        summary = replay(rows, traces)
    except (ValueError, KeyError, TypeError) as error:
        return result | dict(status='fail', errors=[str(error)], candidate_validation=verified)
    return result | summary | dict(status='pass', errors=[], candidate_validation=verified)


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key in publication input')
            result[key] = value
        return result
    return [json.loads(line, object_pairs_hook=unique) for line in Path(path).read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--candidates', required=True, type=Path)
    parser.add_argument('--presented', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    paths = [args.ledger.resolve(), args.candidates.resolve(), args.presented.resolve()]
    if args.out.resolve() in paths:
        parser.error('output cannot overwrite an input')
    digests = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result = evaluate(*(load(p) for p in paths))
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != digests[str(p)] for p in paths):
        raise ValueError('publication inputs changed during analysis')
    result['input_sha256'] = digests
    result['provenance_scope'] = 'Explicit input hashes only; standalone invocation does not establish hardware/build/manifest provenance.'
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'errors': result['errors']}))
    raise SystemExit(0 if result['status'] == 'pass' else 1)


if __name__ == '__main__':
    main()
