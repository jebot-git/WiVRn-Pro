"""Pure, default-off harness adapter for the diagnostic recent-tail pacer.

Acquisition owns the other trace gates and binary/build attestation. This helper
checks reviewed native source, records the exact sidecar bytes it analyzes, and
cannot grant latency or promotion eligibility. It performs no device operations.
"""
from argparse import Namespace
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OVERLAY = Path(__file__).with_name('source-overlay')
ANALYZER = ROOT / 'prototypes/vr444/experiments/pacer_recent_tail/analyze.py'
HELPERS = ('server/compositor/pacer.cpp', 'server/compositor/pacer.h',
           'server/compositor/codec_pacer_trace.h', 'server/compositor/codec_pacer_tail.h')
TRACE = 'host/pacer-tail.jsonl'
MAX_TRACE = 8 * 1024 * 1024


def need(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded(path, limit):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink input: ' + str(path))
    need(path.is_file(), 'missing input: ' + str(path))
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    need(len(raw) <= limit, 'oversized input: ' + str(path))
    return raw


def strict(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate JSON key: ' + key)
            out[key] = value
        return out
    def constant(value):
        raise ValueError('nonfinite JSON: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def window(value):
    need(type(value) is int and (value == 0 or 360 <= value <= 1800),
         'pacer_window_frames must be an integer, 0 or 360..1800')
    return value


def validate(args, source=None):
    """Return reviewed native paths; optional exact source preflight is offline."""
    enabled = window(getattr(args, 'pacer_window_frames', 0))
    if not enabled:
        return ()
    need(getattr(args, 'mode', None) in ('custom-vr444', 'custom-rrb32')
         and getattr(args, 'decoder_backend', None) == 'cpu-neon'
         and all(getattr(args, k, False) is True for k in
                 ('pacer_trace', 'source_trace', 'candidate_trace')),
         'pacer tail requires custom CPU NEON plus pacer, source and candidate diagnostics')
    if source is not None:
        for name in HELPERS:
            need(bounded(Path(source) / name, MAX_TRACE) == bounded(OVERLAY / name, MAX_TRACE),
                 'pacer tail native source differs from reviewed overlay: ' + name)
    return HELPERS


def configure(args, output):
    """Only return environment additions; no native reads or directory creation."""
    validate(args)
    enabled = window(getattr(args, 'pacer_window_frames', 0))
    if not enabled:
        return {}
    return {'WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES': str(enabled),
            'WIVRN_CODEC_TEST_PACER_TAIL_TRACE': str(Path(output).absolute() / TRACE)}


def evaluate(output, enabled_window):
    """Analyze one immutable bounded read; return fail instead of raising.

    hashes.json is created after immediate harness evaluation. If already present
    during replay, its sidecar and provenance bindings are required. Otherwise the
    returned SHA records what was actually parsed, not an external authenticity
    assertion. Existing source/pacer/candidate gates remain the caller's job.
    """
    output = Path(output)
    result = dict(schema=1, diagnostic_only=True, latency_qualified=False,
                  promotion_eligible=False, promotion_blocked=True,
                  other_trace_gates_evaluated=False, native_binary_attested=False)
    inputs = {}
    result['input_artifacts'] = inputs
    def read(name, limit):
        raw = bounded(output / name, limit)
        inputs[name] = dict(sha256=digest(raw), bytes=len(raw))
        return raw
    try:
        enabled = window(enabled_window)
        path = output / TRACE
        if not enabled:
            need(not path.exists() and not path.is_symlink(), 'pacer tail evidence present while disabled')
            return result | dict(status='off')
        provenance = strict(read('provenance.json', 4 * 1024 * 1024))
        need(type(provenance) is dict, 'provenance object required')
        need(window(provenance.get('pacer_window_frames')) == enabled, 'pacer tail request/provenance mismatch')
        validate(Namespace(**provenance))
        recorded = provenance.get('source_helper_sha256')
        need(type(recorded) is dict, 'pacer tail source provenance missing')
        source_hashes = {}
        for name in HELPERS:
            value = recorded.get(name)
            need(type(value) is str and re.fullmatch('[0-9a-f]{64}', value),
                 'pacer tail native source provenance missing: ' + name)
            expected = digest(bounded(OVERLAY / name, MAX_TRACE))
            need(value == expected, 'pacer tail recorded source differs from reviewed overlay: ' + name)
            source_hashes[name] = value
        raw = read(TRACE, MAX_TRACE)
        result['input_sha256'] = digest(raw)
        result['recorded_native_source_sha256'] = source_hashes
        manifest_path = output / 'hashes.json'
        manifest_checked = manifest_path.exists() or manifest_path.is_symlink()
        if manifest_checked:
            manifest = strict(read('hashes.json', 16 * 1024 * 1024))
            need(type(manifest) is dict, 'run hash manifest object required')
            for name in (TRACE, 'provenance.json'):
                need(manifest.get(name) == inputs[name]['sha256'], 'run hash binding mismatch: ' + name)
        result['existing_run_manifest_verified'] = manifest_checked
        # Execute the same source bytes that are hashed below, not a later import
        # read. This module is reviewed local code, never supplied by the capture.
        analyzer_raw = bounded(ANALYZER, 1024 * 1024)
        namespace = {'__name__': 'codec_pacer_tail_analyzer', '__file__': str(ANALYZER)}
        exec(compile(analyzer_raw, str(ANALYZER), 'exec'), namespace)
        rows = [strict(line) for line in raw.splitlines()]
        report = namespace['evaluate'](rows)
        need(report.get('window_frames') == enabled, 'pacer tail header/request mismatch')
        result.update(report)
        result['analyzer_sources_sha256'] = {
            str(ANALYZER.relative_to(ROOT)): digest(analyzer_raw),
            str(Path(__file__).relative_to(ROOT)): digest(bounded(Path(__file__), 1024 * 1024))}
        return result
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, OverflowError) as exc:
        return result | dict(status='fail', errors=[str(exc)])
