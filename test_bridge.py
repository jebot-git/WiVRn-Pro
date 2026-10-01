# SPDX-License-Identifier: MIT
import asyncio
import contextlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from bridge import Broker, MAX_CONNECTIONS, encode, monitor
import catalog


def sample(generation=0):
    return {'type': 'tracking', 'reference_space': 'pc_stage', 'generation': generation,
            'sample_time_ns': '100', 'devices': {'head': {'valid': True,
            'position': [1, 2, 3], 'rotation': [0, 0, 0, 1]}},
            'boundary': {'available': False}}


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.broker = Broker(self.root / 'runtime', ttl=0.05)
        self.endpoint = await self.broker.start()
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
        await self.broker.close()
        self.tmp.cleanup()

    async def connect(self, hello=None):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.endpoint['port'])
        self.writers.append(writer)
        writer.write(encode(hello if hello is not None else {
            'version': 1, 'op': 'subscribe', 'token': self.endpoint['token']}))
        await writer.drain()
        return reader, writer

    async def packet(self, reader):
        return json.loads(await asyncio.wait_for(reader.readline(), 1))

    async def test_two_receivers_and_late_join(self):
        a, _ = await self.connect()
        b, _ = await self.connect()
        await self.packet(a)
        await self.packet(b)
        self.broker.begin_provider()
        await self.packet(a)
        await self.packet(b)
        self.broker.ingest(sample())
        one, two = await self.packet(a), await self.packet(b)
        self.assertEqual(one, two)
        self.assertTrue(one['devices']['head']['valid'])
        late, _ = await self.connect()
        self.assertEqual((await self.packet(late))['devices'], one['devices'])

    async def test_provider_restart_preserves_clients_but_changes_session(self):
        a, _ = await self.connect()
        await self.packet(a)
        self.broker.begin_provider()
        first = await self.packet(a)
        self.broker.ingest(sample())
        await self.packet(a)
        self.broker.invalidate('provider_disconnected')
        disconnected = await self.packet(a)
        self.assertEqual(disconnected['devices'], {})
        self.broker.begin_provider()
        second = await self.packet(a)
        self.assertEqual(first['session'], second['session'])
        self.assertNotEqual(first['provider_session'], second['provider_session'])
        self.broker.ingest(sample(1))
        self.assertEqual((await self.packet(a))['generation'], 1)

    async def test_connection_capacity_fanout_and_reclaimed_slot(self):
        connections = [await self.connect() for _ in range(MAX_CONNECTIONS)]
        await asyncio.gather(*(self.packet(reader) for reader, _ in connections))
        self.assertEqual(len(self.broker.clients), MAX_CONNECTIONS)
        excess, _ = await self.connect()
        # Rejection before reading the hello may produce TCP RST instead of EOF.
        with contextlib.suppress(ConnectionResetError):
            self.assertEqual(await asyncio.wait_for(excess.read(), 1), b'')
        for generation in range(10):
            self.broker.ingest(sample(generation))
            packets = await asyncio.gather(*(self.packet(reader) for reader, _ in connections))
            self.assertTrue(all(packet == packets[0] for packet in packets))
            self.assertEqual(packets[0]['generation'], generation)
        _, departing = connections.pop()
        departing.close()
        await departing.wait_closed()
        async with asyncio.timeout(1):
            while len(self.broker.handlers) != MAX_CONNECTIONS - 1:
                await asyncio.sleep(0.001)
        replacement, _ = await self.connect()
        self.assertEqual((await self.packet(replacement))['sequence'], self.broker.sequence)
        self.broker.ingest(sample(10))
        packets = await asyncio.gather(self.packet(replacement),
            *(self.packet(reader) for reader, _ in connections))
        self.assertTrue(all(packet == packets[0] for packet in packets))
        self.assertEqual(len(self.broker.clients), MAX_CONNECTIONS)

    async def test_quest_provider_fanout_and_disconnect_preserves_receivers(self):
        readers = [(await self.connect())[0] for _ in range(3)]
        await asyncio.gather(*(self.packet(reader) for reader in readers))
        _, provider = await self.connect({'version': 1, 'op': 'provide_quest',
                                          'token': self.broker.provider_token})
        await asyncio.gather(*(self.packet(reader) for reader in readers))
        provider.write(encode({'type': 'quest_snapshot', 'reference_space': 'quest_stage',
            'status': 'tracking', 'resumed': True, 'devices': {},
            'anchors': {'test': {'valid': True}}}))
        await provider.drain()
        packets = await asyncio.gather(*(self.packet(reader) for reader in readers))
        self.assertTrue(all(packet == packets[0] for packet in packets))
        self.assertTrue(packets[0]['quest']['anchors']['test']['valid'])
        provider.close()
        await provider.wait_closed()
        packets = await asyncio.gather(*(self.packet(reader) for reader in readers))
        self.assertTrue(all(packet['quest']['status'] == 'disconnected' and
                            not packet['quest']['anchors'] for packet in packets))
        self.broker.ingest(sample())
        packets = await asyncio.gather(*(self.packet(reader) for reader in readers))
        self.assertTrue(all(packet['status'] == 'tracking' for packet in packets))

    async def test_stale_data_expires_and_late_join_is_invalid(self):
        self.broker.begin_provider()
        self.broker.ingest(sample())
        self.broker.last_sample = time.monotonic() - 1
        a, _ = await self.connect()
        packet = await self.packet(a)
        self.assertEqual(packet['status'], 'stale')
        self.assertEqual(packet['devices'], {})
        self.assertIsNone(packet['boundary'])

    async def test_auth_and_version_rejected(self):
        for hello in ({'version': 1, 'op': 'subscribe', 'token': 'wrong'},
                      {'version': 2, 'op': 'subscribe', 'token': self.endpoint['token']},
                      {'version': 1, 'op': 'write', 'token': self.endpoint['token']}, []):
            reader, _ = await self.connect(hello)
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_malformed_oversize_and_read_only(self):
        for content in (b'invalid\n', b'x' * 70000 + b'\n'):
            reader, writer = await asyncio.open_connection('127.0.0.1', self.endpoint['port'])
            self.writers.append(writer)
            writer.write(content)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        reader, writer = await self.connect()
        await self.packet(reader)
        writer.write(b'{"op":"delete"}\n')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_slow_consumer_cannot_block_healthy_consumer(self):
        class Slow:
            closed = False
            def close(self): self.closed = True
        slow = Slow()
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(b'blocked')
        self.broker.clients[slow] = queue
        reader, _ = await self.connect()
        await self.packet(reader)
        self.broker.ingest(sample())
        self.assertTrue(slow.closed)
        self.assertNotIn(slow, self.broker.clients)
        self.assertEqual((await self.packet(reader))['status'], 'tracking')

    async def test_endpoint_private_and_removed_on_stop(self):
        endpoint = self.root / 'runtime/endpoint.json'
        self.assertEqual(endpoint.stat().st_mode & 0o777, 0o600)
        self.assertEqual(endpoint.parent.stat().st_mode & 0o777, 0o700)
        await self.broker.close()
        self.assertFalse(endpoint.exists())
        self.broker.lock = None

    async def test_catalog_survives_provider_and_service_restart(self):
        path = self.root / 'data/catalog.json'
        source = self.root / 'room.json'
        source.write_text(json.dumps({'version': 2, 'ground': {}, 'obstacles': [], 'spots': []}))
        catalog.import_room(path, 'living_room', source)
        self.broker.catalog_path = path
        self.broker.refresh_catalog()
        saved = self.broker.state['catalog']
        self.broker.begin_provider()
        self.broker.ingest(sample())
        self.broker.invalidate('disconnected')
        self.assertEqual(self.broker.state['catalog'], saved)
        replacement = Broker(self.root / 'other_runtime', catalog_path=path)
        self.assertEqual(replacement.state['catalog'], saved)
        self.assertEqual(saved['rooms']['living_room']['alignment'], 'unresolved')
        source.write_text('{"invalid": true}')
        with self.assertRaises(ValueError):
            catalog.import_room(path, 'bad', source)
        self.assertEqual(catalog.load(path), saved)

    async def test_bad_catalog_keeps_last_good_data(self):
        path = self.root / 'catalog.json'
        self.broker.catalog_path = path
        self.broker.refresh_catalog()
        path.write_text('invalid json')
        self.broker.refresh_catalog()
        self.assertIn('catalog_error', self.broker.state)
        self.assertEqual(self.broker.state['catalog'], catalog.empty())

    async def test_quest_provider_token_is_separate_and_single_writer(self):
        denied, _ = await self.connect({'version': 1, 'op': 'provide_quest', 'token': self.endpoint['token']})
        self.assertEqual(await asyncio.wait_for(denied.read(), 1), b'')
        reader, writer = await self.connect({'version': 1, 'op': 'provide_quest', 'token': self.broker.provider_token})
        for _ in range(3): await asyncio.sleep(0)
        self.assertIsNotNone(self.broker.quest_writer)
        other, _ = await self.connect({'version': 1, 'op': 'provide_quest', 'token': self.broker.provider_token})
        self.assertEqual(await asyncio.wait_for(other.read(), 1), b'')
        value = {'type': 'quest_snapshot', 'reference_space': 'quest_stage', 'status': 'tracking',
                 'resumed': True, 'source_session': 'test', 'devices': {},
                 'anchors': {'test': {'valid': True}}, 'scene': {'entities': []},
                 'capture': {'phase': 'capture', 'target_count': 12, 'verified_count': 0}}
        writer.write(encode(value))
        await writer.drain()
        async with asyncio.timeout(1):
            while self.broker.state['quest']['status'] != 'tracking': await asyncio.sleep(0.001)
        self.assertEqual(self.broker.state['anchors'], {})
        self.assertEqual(self.broker.state['quest']['alignment_to_pc'], 'unresolved')
        self.assertEqual(self.broker.state['quest']['capture'], value['capture'])
        value.update(status='paused', resumed=False)
        writer.write(encode(value))
        await writer.drain()
        async with asyncio.timeout(1):
            while self.broker.state['quest']['status'] != 'paused': await asyncio.sleep(0.001)
        self.assertEqual(self.broker.state['quest']['anchors'], {})

    async def test_quest_expiry_and_persistent_spatial_cache(self):
        path = self.root / 'data/catalog.json'
        self.broker.catalog_path = path
        value = {'type': 'quest_snapshot', 'reference_space': 'quest_stage', 'status': 'tracking',
                 'resumed': True, 'source_session': 'test', 'devices': {},
                 'anchors': {'abcd': {'valid': True, 'persisted': True, 'position': [1,2,3], 'rotation': [0,0,0,1]}},
                 'scene': {'complete': True, 'query_result': 0, 'entities': []}}
        self.broker.ingest_quest(value)
        self.broker.quest_seen = time.monotonic() - 1
        self.broker.expire()
        self.assertEqual(self.broker.state['quest']['anchors'], {})
        archive = self.broker.state['quest_archive']
        self.assertNotIn('valid', archive['markers']['abcd'])
        replacement = Broker(self.root / 'another', catalog_path=path)
        self.assertEqual(replacement.state['quest_archive'], archive)
        self.assertEqual(replacement.state['quest']['status'], 'disconnected')
        self.assertEqual(archive['alignment_to_pc'], 'unresolved')

    async def test_quest_malformed_and_oversize(self):
        for value in ({'type': 'quest_snapshot', 'reference_space': 'pc_stage'},
                      {'type': 'quest_snapshot', 'reference_space': 'quest_stage', 'anchors': []},
                      {'type': 'quest_snapshot', 'reference_space': 'quest_stage', 'scene': 'bad'},
                      {'type': 'quest_snapshot', 'reference_space': 'quest_stage', 'padding': 'x'*25000}):
            with self.assertRaises(ValueError): self.broker.ingest_quest(value)

    async def test_guided_capture_twelve_markers_and_cache(self):
        self.broker.catalog_path = self.root / 'data/catalog.json'
        reader, _ = await self.connect()
        await self.packet(reader)
        markers = {f'{i:032x}': {'valid': True, 'persisted': True, 'verified': True,
            'skipped': False, 'slot': i, 'position': [i/10, 1, -1], 'rotation': [0, 0, 0, 1]}
            for i in range(1, 13)}
        capture = {'version': 1, 'phase': 'complete', 'target_count': 12,
                   'accepted_count': 12, 'verified_count': 12, 'complete': True}
        self.broker.ingest_quest({'type': 'quest_snapshot', 'reference_space': 'quest_stage',
            'source_session': 'guided-test', 'status': 'tracking', 'resumed': True,
            'devices': {}, 'anchors': markers, 'capture': capture})
        packet = await self.packet(reader)
        self.assertEqual(packet['quest']['anchors'], markers)
        self.assertEqual(packet['quest']['capture'], capture)
        archive = packet['quest_archive']
        self.assertEqual(len(archive['markers']), 12)
        self.assertTrue(all('valid' not in marker and 'verified' not in marker
                            for marker in archive['markers'].values()))
        self.broker.invalidate_quest('paused')
        packet = await self.packet(reader)
        self.assertEqual(packet['quest']['anchors'], {})
        self.assertEqual(packet['quest_archive'], archive)

    async def test_monitor_restarts_provider_when_unique_bus_owner_changes(self):
        status = {'available': True, 'headset_connected': True, 'session_running': True, 'owner': ':1.5'}
        starts = []
        cancelled = []
        real_sleep = asyncio.sleep
        async def installed(): return dict(status)
        async def provider(broker, helper, runtime):
            starts.append(status['owner'])
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        async def fast_sleep(_): await real_sleep(0.001)
        with patch('bridge.installed_status', installed), patch('bridge.read_provider', provider), patch('bridge.asyncio.sleep', fast_sleep), patch('bridge.RECONNECT_GRACE', 0):
            task = asyncio.create_task(monitor(self.broker, True, None, None))
            try:
                while not starts: await real_sleep(0.001)
                status['owner'] = ':1.8'
                async with asyncio.timeout(1):
                    while len(starts) < 2: await real_sleep(0.001)
                status['headset_connected'] = False
                async with asyncio.timeout(1):
                    while len(cancelled) < 2: await real_sleep(0.001)
                self.assertEqual(starts, [':1.5', ':1.8'])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_short_headset_pause_keeps_helper_but_withholds_poses(self):
        status = {'available': True, 'headset_connected': True, 'session_running': True, 'owner': ':1.5'}
        starts = []
        cancelled = []
        real_sleep = asyncio.sleep
        async def installed(): return dict(status)
        async def provider(broker, helper, runtime):
            broker.begin_provider()
            starts.append(broker.state['provider_session'])
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        async def fast_sleep(_): await real_sleep(.001)
        with patch('bridge.installed_status', installed), patch('bridge.read_provider', provider), patch('bridge.asyncio.sleep', fast_sleep):
            task = asyncio.create_task(monitor(self.broker, True, None, None))
            try:
                async with asyncio.timeout(1):
                    while not starts: await real_sleep(.001)
                self.broker.ingest(sample())
                status['headset_connected'] = False
                async with asyncio.timeout(1):
                    while not self.broker.provider_paused: await real_sleep(.001)
                self.broker.ingest(sample())
                self.assertEqual(self.broker.state['devices'], {})
                self.assertEqual(self.broker.state['status'], 'disconnected')
                self.assertFalse(cancelled)
                status['headset_connected'] = True
                async with asyncio.timeout(1):
                    while self.broker.provider_paused: await real_sleep(.001)
                self.broker.ingest(sample())
                self.assertEqual(self.broker.state['status'], 'tracking')
                self.assertEqual(self.broker.state['provider_session'], starts[0])
                self.assertEqual(len(starts), 1)
                self.assertFalse(cancelled)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
