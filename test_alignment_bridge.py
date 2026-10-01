# SPDX-License-Identifier: MIT
import asyncio
import copy
import json
import math
from pathlib import Path
import tempfile
import time
import unittest

import alignment
from bridge import Broker, LIMIT, encode
from controller_calibration import submit
from receiver import subscribe


class AlignmentBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)/'runtime'
        self.broker = Broker(self.directory)
        self.endpoint = await self.broker.start()
        b = self.broker
        b.state['wivrn'] = {'owner': ':1.1', 'available': True, 'session_running': True, 'headset_connected': True}
        b.begin_provider()
        self.transform = {'position': [.7, -.03, -1.2], 'rotation': [0, math.sin(.8), 0, math.cos(.8)]}
        qposes = {'left_hand': {'position': [-.4, .8, -.6], 'rotation': alignment.unit([.4, .2, .1, .7])},
                  'right_hand': {'position': [.8, .7, -.9], 'rotation': alignment.unit([-.2, .3, .6, .1])}}
        pposes = {h: alignment.apply(self.transform, p) for h, p in qposes.items()}
        self.samples = {}
        for phase, poses, session, offset in (
                ('pc_before', pposes, b.state['provider_session'], 0),
                ('quest', qposes, 'quest-test', 0),
                ('pc_after', pposes, b.state['provider_session'], 10_000_000_000)):
            rows = []
            for i in range(41):
                devices = copy.deepcopy(poses)
                for p in devices.values():
                    p.update(valid=True, flags=15, pose_space='grip', interaction_profile='touch')
                rows.append({'identity': [b.session, session, 1], 'sample_time_ns': str(offset+i*100_000_000), 'devices': devices})
            self.samples[phase] = rows
        self.pc = {'type': 'tracking', 'reference_space': 'pc_stage', 'generation': 1,
                   'sample_time_ns': '14000000000', 'devices': pposes}
        b.ingest(self.pc)
        self.quest = {'type': 'quest_snapshot', 'reference_space': 'quest_stage', 'source_session': 'quest-test',
                      'generation': 1, 'status': 'tracking', 'resumed': True, 'xr_state': 5,
                      'sample_time_ns': '4000000000', 'devices': {},
                      'anchors': {'a': dict(qposes['left_hand'], valid=True, flags=15, persisted=True)},
                      'scene': {'entities': [{'uuid': 'floor', 'pose': dict(qposes['right_hand'], valid=True, flags=15),
                                              'boundary_2d': [[0, 0], [1, 1]]}]},
                      'boundary': {'available': True, 'width': 3, 'depth': 4}}
        b.ingest_quest(copy.deepcopy(self.quest))

    async def asyncTearDown(self):
        await self.broker.close()
        self.temp.cleanup()

    def activate(self):
        return self.broker.apply_controller_calibration(self.samples)

    async def test_transforms_live_and_cached_without_mutating_raw_or_geometry(self):
        identifier = self.activate()
        packet = self.broker.snapshot()
        self.assertEqual(packet['alignment']['calibration_id'], identifier)
        self.assertEqual(packet['alignment']['status'], 'valid')
        self.assertEqual(packet['quest']['anchors'], self.quest['anchors'])
        self.assertEqual(packet['quest']['scene'], self.quest['scene'])
        anchor = packet['quest_aligned']['anchors']['a']
        expected = alignment.apply(self.transform, self.quest['anchors']['a'])
        self.assertLess(alignment.residual(anchor, expected)['position_m'], 1e-10)
        floor = packet['quest_aligned']['scene']['entities'][0]
        expected_floor = alignment.apply(self.transform, self.quest['scene']['entities'][0]['pose'])
        self.assertLess(alignment.residual(floor['pose'], expected_floor)['position_m'], 1e-10)
        self.assertNotIn('boundary_2d', floor)
        boundary = packet['quest_aligned']['boundary']
        self.assertEqual(boundary['width'], 3)
        self.assertAlmostEqual(boundary['pose']['position'][0], .7)
        cached = packet['quest_archive_aligned']
        self.assertEqual(cached['status'], 'cached')
        self.assertFalse(cached['live'])
        self.assertNotIn('valid', cached['markers']['a']['saved_pose'])
        self.assertNotIn('valid', cached['scene']['entities'][0]['saved_pose'])
        self.assertEqual(packet['anchors'], {})

    async def test_pause_sparse_packets_keep_cached_alignment_but_not_live_poses(self):
        self.activate()
        self.broker.ingest_quest({'type': 'quest_snapshot', 'reference_space': 'quest_stage',
                                 'source_session': 'quest-test', 'status': 'paused', 'resumed': False})
        packet = self.broker.snapshot()
        self.assertEqual(packet['alignment']['status'], 'valid')
        self.assertEqual(packet['quest_aligned']['status'], 'unavailable')
        self.assertEqual(packet['quest_archive_aligned']['status'], 'cached')
        self.broker.invalidate_quest('disconnected')
        self.assertEqual(self.broker.snapshot()['quest_archive_aligned']['status'], 'cached')

    async def test_invalidation_is_sticky_for_pc_generation_or_tracking_loss(self):
        self.activate()
        self.broker.ingest(dict(self.pc, generation=2))
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'invalidated')
        self.broker.ingest(self.pc)
        self.assertEqual(self.broker.snapshot()['quest_archive_aligned']['status'], 'unavailable')
        self.activate()
        self.broker.last_sample = time.monotonic()-1
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'invalidated')
        self.broker.ingest(self.pc)
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'invalidated')

    async def test_quest_relaunch_or_recenter_invalidates(self):
        self.activate()
        self.broker.ingest_quest(dict(self.quest, generation=2))
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'invalidated')
        self.broker.ingest_quest(self.quest)
        self.activate()
        self.broker.ingest_quest({'type': 'quest_snapshot', 'reference_space': 'quest_stage',
                                 'source_session': 'new-quest', 'status': 'starting'})
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'invalidated')

    async def test_owner_change_invalidates_and_restart_cannot_reuse_report(self):
        self.activate()
        self.broker.state['wivrn']['owner'] = ':1.2'
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'invalidated')
        self.broker.begin_provider()
        self.broker.ingest(self.pc)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.activate()
        fresh = Broker(Path(self.temp.name)/'new')
        self.assertEqual(fresh.snapshot()['alignment']['status'], 'unresolved')

    async def test_archive_from_different_quest_session_is_not_transformed(self):
        self.broker.state['quest_archive']['source_session'] = 'older'
        self.activate()
        packet = self.broker.snapshot()
        self.assertEqual(packet['quest_aligned']['status'], 'tracking')
        self.assertEqual(packet['quest_archive_aligned']['reason'], 'archive_source_mismatch')

    async def test_scene_only_is_cached_and_invalid_poses_are_omitted(self):
        self.quest['anchors'] = {}
        self.quest['devices'] = {'bad': {'valid': True, 'position': [0, 0, 0], 'rotation': [0, 0, 0, 0]}}
        self.quest['scene']['entities'].append({'uuid': 'invalid', 'pose': {'valid': False}})
        self.broker.ingest_quest(self.quest)
        self.activate()
        packet = self.broker.snapshot()
        self.assertEqual(packet['quest_aligned']['devices'], {})
        self.assertEqual(len(packet['quest_archive_aligned']['scene']['entities']), 1)
        self.assertEqual(len(packet['quest_aligned']['scene']['entities']), 1)

    async def test_old_or_inconsistent_report_cannot_replace_active_calibration(self):
        identifier = self.activate()
        self.broker.ingest(dict(self.pc, sample_time_ns='50000000000'))
        with self.assertRaisesRegex(ValueError, 'last 30 seconds'):
            self.activate()
        self.broker.ingest(self.pc)
        bad = copy.deepcopy(self.samples)
        for row in bad['quest']:
            row['devices']['right_hand']['position'][0] += .1
        with self.assertRaisesRegex(ValueError, 'Independent controller'):
            self.broker.apply_controller_calibration(bad)
        self.assertEqual(self.broker.snapshot()['alignment']['calibration_id'], identifier)

    async def test_private_control_protocol_fanout_late_join_and_clear(self):
        path = self.directory/'calibration-provider.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(json.loads(path.read_text())['token'], self.endpoint['token'])
        clients = [subscribe(self.directory/'endpoint.json') for _ in range(2)]
        try:
            await asyncio.gather(*(anext(c) for c in clients))
            result = await submit(self.directory, self.samples)
            packets = await asyncio.gather(*(anext(c) for c in clients))
            self.assertEqual(packets[0], packets[1])
            self.assertEqual(packets[0]['alignment']['calibration_id'], result['calibration_id'])
            late = subscribe(self.directory/'endpoint.json')
            try:
                self.assertEqual((await anext(late))['alignment']['calibration_id'], result['calibration_id'])
            finally:
                await late.aclose()
            await submit(self.directory, clear=True)
            cleared = await asyncio.gather(*(anext(c) for c in clients))
            self.assertTrue(all(p['alignment']['status'] == 'invalidated' for p in cleared))
        finally:
            await asyncio.gather(*(c.aclose() for c in clients))

    async def test_subscriber_and_quest_credentials_cannot_calibrate(self):
        for token in (self.broker.token, self.broker.provider_token):
            reader, writer = await asyncio.open_connection(self.endpoint['host'], self.endpoint['port'])
            writer.write(encode({'version': 1, 'op': 'calibrate_controllers', 'token': token}))
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
            writer.close()
            await writer.wait_closed()
        self.assertEqual(self.broker.snapshot()['alignment']['status'], 'unresolved')

    async def test_protocol_rejects_malformed_report_and_keeps_server_healthy(self):
        with self.assertRaises(ValueError):
            await submit(self.directory, dict.fromkeys(('pc_before', 'quest', 'pc_after'), [{}]*30))
        result = await submit(self.directory, self.samples)
        self.assertTrue(result['accepted'])

    async def test_snapshot_budget_omits_derived_views_first(self):
        self.activate()
        base = len(encode(self.broker.snapshot()))
        self.broker.state['catalog']['padding'] = 'x'*(LIMIT-base+100)
        packet = self.broker.snapshot()
        self.assertLessEqual(len(encode(packet)), LIMIT)
        self.assertEqual(packet['quest_archive_aligned']['reason'], 'snapshot_size_limit')


if __name__ == '__main__':
    unittest.main()
