#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Opt-in live check: stock WiVRn must already be running. Never restarts it."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
from receiver import subscribe
from bridge import installed_status

ROOT = Path(__file__).resolve().parent


async def main():
    (ROOT / "evidence").mkdir(exist_ok=True)
    before = await installed_status()
    if not before.get('session_running') or not before.get('headset_connected'):
        raise RuntimeError('Start/connect stock WiVRn yourself before running this opt-in test')
    with tempfile.TemporaryDirectory(prefix='room-bridge-live-') as temp:
        directory = Path(temp) / 'runtime'
        endpoint = directory / 'endpoint.json'
        log = open(Path(temp) / 'bridge.log', 'wb')
        process = await asyncio.create_subprocess_exec('python3', str(ROOT / 'bridge.py'),
            '--track', '--directory', str(directory), '--catalog', str(Path(temp)/'catalog.json'),
            stdout=log, stderr=log)
        consumers = []
        try:
            async with asyncio.timeout(10):
                while not endpoint.exists():
                    if process.returncode is not None:
                        raise RuntimeError('Bridge exited; see log')
                    await asyncio.sleep(0.05)
            records = [[], []]
            async def consume(index):
                async for packet in subscribe(endpoint):
                    records[index].append(packet)
            consumers = [asyncio.create_task(consume(i)) for i in range(2)]
            async def wait_for(predicate):
                async with asyncio.timeout(15):
                    while not predicate():
                        for task in consumers:
                            if task.done(): task.result()
                        await asyncio.sleep(0.05)
            await wait_for(lambda: all(any(x['status']=='tracking' for x in group) for group in records))
            first = next(x for x in records[0] if x['status']=='tracking')
            # Only terminate our own helper, never WiVRn. Receiver TCP sessions stay open.
            children = Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
            killed = False
            for child in children:
                if Path(f'/proc/{child}/comm').read_text().strip() == 'tracking-source':
                    os.kill(int(child), signal.SIGTERM)
                    killed = True
            assert killed, 'No owned tracking helper found'
            await wait_for(lambda: all(any(x['status']=='tracking' and
                x['provider_session'] != first['provider_session'] for x in group) for group in records))
            assert all(any(x['status']=='provider_disconnected' and not x['devices'] for x in group) for group in records)
            assert all(all(x['session']==first['session'] for x in group) for group in records)
            # A third Python receiver joins the same broker after recovery.
            records.append([])
            consumers.append(asyncio.create_task(consume(2)))
            def valid_late_samples():
                return sum(x['status'] == 'tracking' and x['devices'].get('head', {}).get('valid', False)
                           for x in records[2])
            def shared_sequences():
                return set.intersection(*(set(x['sequence'] for x in group) for group in records))
            await wait_for(lambda: valid_late_samples() >= 10 and len(shared_sequences()) >= 5)
            assert all(x['session'] == first['session'] for x in records[2])
            after = await installed_status()
            assert before['owner'] == after['owner'] and after['session_running']
            shared = shared_sequences()
            assert len(shared) >= 5
            report = {'ok': True, 'runtime': first.get('runtime'), 'receiver_count': 3,
                      'python_receivers_shared_snapshots': len(shared),
                      'late_receiver_valid_headset_samples': valid_late_samples(),
                      'provider_restart_kept_receiver_connections': True,
                      'wivrn_restart_performed': False, 'stock_wivrn_owner_unchanged': True,
                      'native_anchors': first['capabilities']['native_anchors'],
                      'boundary': first['boundary'],
                      'device_validity': {key:value['valid'] for key,value in first['devices'].items()}}
            (ROOT / 'evidence/room_bridge_live_smoke.json').write_text(json.dumps(report, indent=2)+'\n')
            print(json.dumps(report, indent=2))
        finally:
            for task in consumers: task.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)
            if process.returncode is None:
                process.terminate()
                try: await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            if process.returncode not in (None, 0):
                print((Path(temp)/'bridge.log').read_text()[-4000:])
            log.close()


if __name__ == '__main__':
    asyncio.run(main())
