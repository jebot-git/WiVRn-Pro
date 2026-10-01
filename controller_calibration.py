#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Measure stationary controller grip poses across Quest/WiVRn app switches.

Measures and activates a session-scoped calibration in the running bridge.
Use --report-only to measure without activation.
Both controllers must remain physically stationary throughout the entire run.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

from bridge import VERSION, LIMIT, default_directory, encode
from receiver import subscribe

from alignment import HANDS, summarize, solve


async def collect(endpoint, source, timeout=60):
    """Wait for an uninterrupted four-second stationary window, rejecting stale data."""
    deadline = time.monotonic()+timeout
    window = []
    last = None
    last_received = 0
    async for packet in subscribe(endpoint):
        if time.monotonic() > deadline:
            raise RuntimeError(f'{source}: no stable tracked controller window within {timeout}s')
        state = packet.get('quest', {}) if source == 'quest' else packet
        age = packet.get('quest_age_ms' if source == 'quest' else 'sample_age_ms')
        identity = [packet['session'], state.get('source_session' if source == 'quest' else 'provider_session'), state.get('generation')]
        devices = state.get('devices', {})
        ready = (state.get('status') == 'tracking' and age is not None and age < 250
                 and identity[1] is not None and identity[2] is not None)
        if source == 'quest':
            ready = ready and state.get('resumed') and state.get('xr_state') == 5
        else:
            ready = ready and packet.get('wivrn', {}).get('headset_connected', False)
        ready = ready and all(devices.get(h, {}).get('valid') and devices[h].get('flags', 0) & 15 == 15 for h in HANDS)
        if not ready:
            window = []
            continue
        stamp = state.get('sample_time_ns')
        key = (tuple(identity), stamp)
        if key == last:
            continue
        now = time.monotonic()
        if window and (window[-1]['identity'] != identity or now-last_received > .5):
            window = []
        last, last_received = key, now
        window.append({'identity': identity, 'sample_time_ns': stamp, 'devices': {h: devices[h] for h in HANDS}})
        window = [s for s in window if int(stamp)-int(s['sample_time_ns']) <= 4_500_000_000]
        if len(window) >= 30 and int(stamp)-int(window[0]['sample_time_ns']) >= 4_000_000_000:
            try:
                summary = summarize(window)
            except ValueError:
                continue
            print(f'{source}: {len(window)} stationary samples; jitter {summary["stability"]}', flush=True)
            return window
    raise RuntimeError('Broker disconnected')


async def command(*args):
    proc = await asyncio.create_subprocess_exec(*map(str, args), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        result = await asyncio.wait_for(proc.wait(), 25)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError('App-switch command timed out') from None
    if result:
        raise RuntimeError('App-switch command failed')


async def submit(directory, samples=None, *, clear=False):
    config = json.loads((directory/'calibration-provider.json').read_text())
    if config.get('version') != VERSION or config.get('host') != '127.0.0.1':
        raise ValueError('Unsupported calibration endpoint')
    reader, writer = await asyncio.wait_for(asyncio.open_connection(config['host'], config['port'], limit=LIMIT), 3)
    try:
        writer.write(encode({'version': VERSION, 'op': 'clear_calibration' if clear else 'calibrate_controllers',
                             'token': config['token']}))
        if not clear:
            for phase in ('pc_before', 'quest', 'pc_after'):
                raw = encode({'phase': phase, 'samples': samples[phase]})
                if len(raw) > LIMIT:
                    raise ValueError('Calibration phase exceeds protocol limit')
                writer.write(raw)
                await asyncio.wait_for(writer.drain(), 3)
        else:
            await asyncio.wait_for(writer.drain(), 3)
        line = await asyncio.wait_for(reader.readline(), 10)
        if not line:
            raise RuntimeError('Bridge closed calibration connection')
        result = json.loads(line)
        if result.get('type') != 'calibration_result' or result.get('accepted') is not True:
            raise ValueError(result.get('error', 'Calibration rejected by bridge'))
        return result
    finally:
        writer.close()
        await writer.wait_closed()


async def run(args):
    directory = args.directory or default_directory()
    if args.clear or args.apply_report:
        try:
            samples = None
            if args.apply_report:
                samples = json.loads(args.apply_report.read_text())['samples']
            result = await submit(directory, samples, clear=args.clear)
        except (RuntimeError, ValueError, OSError, KeyError, asyncio.TimeoutError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0
    endpoint = directory/'endpoint.json'
    root = Path(__file__).resolve().parent
    adb = ['adb'] + (['-s', args.serial] if args.serial else [])
    report = {'version': 1, 'created_unix_ns': str(time.time_ns()), 'status': 'incomplete', 'samples': {}}
    switched = False
    try:
        print('Measuring WiVRn controllers before switching. Keep both controllers stationary.', flush=True)
        report['samples']['pc_before'] = await collect(endpoint, 'pc', args.timeout)
        print('Switching to Quest controller capture.', flush=True)
        switched = True
        launch = [sys.executable, root/'quest/launch.py', '--endpoint', directory/'quest-provider.json', '--calibration']
        if args.serial:
            launch += ['--serial', args.serial]
        await command(*launch)
        report['samples']['quest'] = await collect(endpoint, 'quest', args.timeout)
        print('Returning to WiVRn for the independent return measurement.', flush=True)
        await command(*adb, 'shell', 'am', 'start', '-n', args.wivrn_activity)
        report['samples']['pc_after'] = await collect(endpoint, 'pc', args.timeout)
        report.update(solve(*(report['samples'][p] for p in ('pc_before', 'quest', 'pc_after'))))
        if not args.report_only:
            report['application'] = await submit(directory, report['samples'])
    except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as error:
        report.update(status='failed', error=str(error))
    finally:
        if switched:
            try:
                await command(*adb, 'shell', 'am', 'start', '-n', args.wivrn_activity)
            except (RuntimeError, OSError) as error:
                report['restore_error'] = str(error)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('samples', 'phases')}, indent=2))
    return 0 if report['status'] == 'validated_for_measured_sessions' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--wivrn-activity', help='Installed WiVRn component, e.g. org.meumeu.wivrn/.MainActivity')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--output', type=Path, default=Path('evidence/controller-calibration.json'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--report-only', action='store_true', help='Measure without activating the result')
    mode.add_argument('--apply-report', type=Path, help='Activate a report from these same sessions, with a return measurement less than 30 seconds old')
    mode.add_argument('--clear', action='store_true', help='Invalidate the active calibration')
    args = parser.parse_args()
    if not args.clear and not args.apply_report and not args.wivrn_activity:
        parser.error('--wivrn-activity is required for physical calibration')
    sys.exit(asyncio.run(run(args)))
