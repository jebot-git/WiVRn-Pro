#!/usr/bin/env python3
"""Opt-in hardware test of the separate Quest source and two PC receivers."""
import argparse
import asyncio
import json
from pathlib import Path
from receiver import subscribe

async def main(args):
    rows=[[],[]]
    async def receive(i):
        async for packet in subscribe(args.endpoint):
            rows[i].append(packet)
    tasks=[asyncio.create_task(receive(i)) for i in range(2)]
    async def until(test):
        async with asyncio.timeout(args.timeout):
            while not test():
                for task in tasks:
                    if task.done(): task.result()
                await asyncio.sleep(0.05)
    def verified_markers(q):
        return {key: value for key, value in q.get('anchors', {}).items()
                if value.get('valid') and value.get('persisted') and value.get('verified') and not value.get('skipped')}
    def valid(p):
        q=p['quest']
        return q.get('status')=='tracking' and len(verified_markers(q))==args.expected_markers and \
            q.get('capture',{}).get('phase') in ('complete','partial') and \
            q.get('capture',{}).get('verified_count')==args.expected_markers
    async def activity(name):
        adb = ['adb'] + (['-s', args.serial] if args.serial else [])
        proc=await asyncio.create_subprocess_exec(*adb,'shell','am','start','-n',name,stdout=asyncio.subprocess.DEVNULL)
        assert await proc.wait()==0
    try:
        await until(lambda:all(sum(valid(p) for p in group)>=10 for group in rows))
        if args.observe_seconds:
            await asyncio.sleep(min(args.observe_seconds, 30))
        first=next(p for p in rows[0] if valid(p))
        archive=first.get('quest_archive')
        assert archive and set(verified_markers(first['quest'])) <= set(archive['markers'])
        assert archive['alignment_to_pc']=='unresolved'
        if args.cycle_wivrn:
            count=len(rows[0])
            await activity('org.meumeu.wivrn/.MainActivity')
            await until(lambda:any(not p['quest'].get('anchors') for p in rows[0][count:]))
            await asyncio.sleep(3)
            paused=rows[0][-1]
            assert paused['quest']['status']!='tracking' and not paused['quest']['anchors']
            assert paused['quest_archive']['markers']==archive['markers']
            count=len(rows[0])
            await activity('org.vrmcompanion.roomsource/.MainActivity')
            await until(lambda:any(valid(p) for p in rows[0][count:]))
            last=next(p for p in rows[0][count:] if valid(p))
            assert set(last['quest']['anchors'])==set(first['quest']['anchors'])
            assert last['session']==first['session']
        shared=set(p['sequence'] for p in rows[0]) & set(p['sequence'] for p in rows[1])
        assert len(shared)>=5
        q=next(p['quest'] for p in reversed(rows[0]) if valid(p))
        report={'ok':True,'receiver_count':2,'shared_snapshots':len(shared),
                'capture':q['capture'],
                'same_markers_after_activity_cycle':True if args.cycle_wivrn else None,
                'saved_localized_markers':len(verified_markers(q)), 'boundary':q['boundary'],
                'scene_query_result':(q.get('scene') or {}).get('query_result'),
                'scene_entity_count':len((q.get('scene') or {}).get('entities',[])),
                'scene_truncated':(q.get('scene') or {}).get('truncated'),
                'scene_labels':sorted(set(x.get('labels','') for x in (q.get('scene') or {}).get('entities',[]))),
                'background_test_performed':args.cycle_wivrn,
                'background_continuous_tracking':False if args.cycle_wivrn else None,
                'cache_retained_when_paused':True if args.cycle_wivrn else None,
                'preview_resumed':args.cycle_wivrn,
                'adb_transport':args.serial or 'default'}
        report['observation_seconds'] = args.observe_seconds
        if args.serial:
            proc = await asyncio.create_subprocess_exec('adb','devices','-l',stdout=asyncio.subprocess.PIPE)
            output, _ = await proc.communicate()
            report['usb_adb_present'] = 'usb:' in output.decode()
            proc = await asyncio.create_subprocess_exec('adb','-s',args.serial,'reverse','--list',stdout=asyncio.subprocess.PIPE)
            output, _ = await proc.communicate()
            report['wireless_reverse_tunnel'] = output.decode().strip()
            if args.require_wireless:
                assert not report['usb_adb_present']
                assert ':' in args.serial and 'host-' in report['wireless_reverse_tunnel']
        path = args.report or Path(__file__).resolve().parent/'evidence/quest_room_source_hardware.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint',required=True,type=Path)
    parser.add_argument('--cycle-wivrn',action='store_true')
    parser.add_argument('--serial', help='ADB transport to use for the activity-cycle test')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--observe-seconds', type=float, default=0)
    parser.add_argument('--require-wireless', action='store_true')
    parser.add_argument('--expected-markers', type=int, choices=range(1,13), default=12)
    parser.add_argument('--timeout', type=float, default=300, help='Seconds to allow for the user-guided capture')
    asyncio.run(main(parser.parse_args()))
