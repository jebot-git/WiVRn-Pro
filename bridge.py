#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only WiVRn binding and authenticated, multi-reader local pose broker."""
import argparse
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import copy
import time
import uuid
import catalog
import alignment

VERSION = 1
LIMIT = 131072
TTL = 0.75
MAX_CONNECTIONS = 32  # Subscribers, Quest provider and pending handshakes combined.
RECONNECT_GRACE = 10.0  # Keep the OpenXR helper through brief Quest activity handoffs.
SERVICE = 'io.github.wivrn.Server'
OBJECT = '/io/github/wivrn/Server'


def encode(value):
    return (json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n').encode()


def default_directory():
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base:
        raise RuntimeError('XDG_RUNTIME_DIR is required (or pass --directory)')
    return Path(base) / 'vrm-room-bridge'


async def dbus_call(destination, path, method, *args):
    proc = await asyncio.create_subprocess_exec(
        'gdbus', 'call', '--session', '--dest', destination,
        '--object-path', path, '--method', method, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 2)
        return output.decode().strip() if proc.returncode == 0 else ''
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def installed_status():
    # Resolve the unique owner first: never activate/start WiVRn via a property read.
    owner_text = await dbus_call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                'org.freedesktop.DBus.GetNameOwner', SERVICE)
    match = re.fullmatch(r"\('(:[0-9]+\.[0-9]+)',\)", owner_text)
    if not match:
        return {'available': False, 'headset_connected': False, 'session_running': False}
    owner = match[1]
    async def boolean(prop):
        value = await dbus_call(owner, OBJECT, 'org.freedesktop.DBus.Properties.Get', SERVICE, prop)
        return value == '(<true>,)'
    connected, running = await asyncio.gather(boolean('HeadsetConnected'), boolean('SessionRunning'))
    return {'available': True, 'owner': owner, 'headset_connected': connected, 'session_running': running}


class Broker:
    def __init__(self, directory, ttl=TTL, catalog_path=None):
        self.directory = Path(directory)
        self.ttl = ttl
        self.token = secrets.token_urlsafe(32)
        self.provider_token = secrets.token_urlsafe(32)
        self.calibration_token = secrets.token_urlsafe(32)
        self.calibration = None
        self.provider_paused = False
        self.quest_identity = None
        self.quest_writer = None
        self.quest_seen = 0.0
        self.quest_archive_key = None
        self.session = str(uuid.uuid4())
        self.sequence = 0
        self.clients = {}  # writer -> bounded queue; one slow app cannot block another
        self.handlers = set()
        self.server = None
        self.lock = None
        self.last_sample = 0.0
        self.catalog_path = catalog_path
        self.state = {'status': 'waiting', 'provider_session': None, 'generation': 0,
                      'reference_space': 'pc_stage', 'devices': {}, 'anchors': {},
                      'boundary': None, 'wivrn': {}, 'catalog': catalog.empty(),
                      'alignment': {'status': 'unresolved', 'method': 'stationary_controllers'},
                      'quest': {'status': 'disconnected', 'reference_space': 'quest_stage',
                                'alignment_to_pc': 'unresolved', 'devices': {}, 'anchors': {}},
                      'capabilities': {'native_anchors': False, 'scene_geometry': False,
                                       'guardian_polygon': False, 'headless': False}}
        self.refresh_catalog()
        self.state['quest_archive'] = None
        if self.catalog_path:
            try:
                archive_path = Path(self.catalog_path).with_name('quest-spatial-cache.json')
                with archive_path.open('rb') as source:
                    raw = source.read(32769)
                if len(raw) <= 32768:
                    value = json.loads(raw)
                    if isinstance(value, dict) and value.get('version') == 1:
                        value['alignment_to_pc'] = 'unresolved'
                        self.state['quest_archive'] = value
            except (OSError, ValueError):
                pass

    def refresh_catalog(self):
        if self.catalog_path is None:
            return
        try:
            data = catalog.load(self.catalog_path)
            changed = data != self.state['catalog'] or 'catalog_error' in self.state
            self.state['catalog'] = data
            self.state.pop('catalog_error', None)
        except (OSError, ValueError) as error:
            changed = self.state.get('catalog_error') != str(error)
            self.state['catalog_error'] = str(error)
        if changed:
            self.publish()

    async def start(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not self.directory.is_dir() or self.directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Discovery directory must be owned by you with mode 0700')
        fd = os.open(self.directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, 'w')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.server = await asyncio.start_server(self.accept, '127.0.0.1', 0, limit=LIMIT)
        endpoint = {'version': VERSION, 'host': '127.0.0.1',
                    'port': self.server.sockets[0].getsockname()[1], 'token': self.token}
        temp = self.directory / ('endpoint.' + self.session)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(endpoint))
        temp.replace(self.directory / 'endpoint.json')
        provider = dict(endpoint, token=self.provider_token)
        fd = os.open(self.directory / 'quest-provider.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(provider))
        control = dict(endpoint, token=self.calibration_token)
        fd = os.open(self.directory / 'calibration-provider.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(control))
        return endpoint

    def snapshot(self):
        self.refresh_alignment()
        packet = {'version': VERSION, 'type': 'snapshot', 'session': self.session,
                'sequence': self.sequence, 'ttl_ms': round(self.ttl * 1000),
                'sample_age_ms': round((time.monotonic() - self.last_sample) * 1000) if self.last_sample else None,
                'quest_age_ms': round((time.monotonic() - self.quest_seen) * 1000) if self.quest_seen else None,
                'sent_monotonic_ns': str(time.monotonic_ns()), **self.state}
        # Derived pose views are optional; never grow beyond the receiver line budget.
        if len(encode(packet)) > LIMIT:
            for field in ('quest_archive_aligned', 'quest_aligned'):
                packet[field] = {'status': 'unavailable', 'reason': 'snapshot_size_limit', 'reference_space': 'pc_stage'}
                if len(encode(packet)) <= LIMIT:
                    break
        return packet

    def invalidate_alignment(self, reason):
        if self.calibration is not None:
            self.state['alignment'] = {'status': 'invalidated', 'method': 'stationary_controllers',
                'calibration_id': self.calibration['calibration_id'], 'reason': reason}
            self.calibration = None

    def pc_identity(self):
        return [self.session, self.state.get('provider_session'), self.state.get('generation')]

    def pc_ready(self):
        wivrn = self.state['wivrn']
        return (self.state['status'] == 'tracking' and self.last_sample
                and time.monotonic()-self.last_sample <= self.ttl
                and wivrn.get('headset_connected') and wivrn.get('session_running'))

    def refresh_alignment(self):
        current = self.calibration
        if current is not None:
            reason = None
            if current['pc_identity'] != self.pc_identity():
                reason = 'pc_session_or_generation_changed'
            elif current['quest_identity'] != self.quest_identity:
                reason = 'quest_session_or_generation_changed'
            elif current['wivrn_owner'] != self.state['wivrn'].get('owner'):
                reason = 'wivrn_owner_changed'
            elif not self.pc_ready():
                reason = 'pc_tracking_unavailable'
            if reason:
                self.invalidate_alignment(reason)
        current = self.calibration
        quest = self.state['quest']
        archive = self.state.get('quest_archive')
        quest['alignment_to_pc'] = 'calibrated' if current else 'unresolved'
        if archive:
            matches = (current is not None and current['quest_identity'][1:] ==
                       [archive.get('source_session'), archive.get('generation')])
            archive['alignment_to_pc'] = 'calibrated' if matches else 'unresolved'
        else:
            matches = False
        unavailable = {'status': 'unavailable', 'reference_space': 'pc_stage', 'live': False}
        self.state['quest_aligned'] = dict(unavailable, reason='quest_not_live' if current else 'alignment_unresolved')
        self.state['quest_archive_aligned'] = dict(unavailable, reason='archive_source_mismatch' if current else 'alignment_unresolved')
        if current is None:
            return
        transform = current['transform']
        if (quest.get('status') == 'tracking' and quest.get('resumed') and quest.get('xr_state') == 5
                and self.quest_seen and time.monotonic()-self.quest_seen <= self.ttl):
            view = alignment.project(transform, quest)
            view.update(status='tracking', calibration_id=current['calibration_id'])
            self.state['quest_aligned'] = view
        if matches:
            view = alignment.project(transform, archive, saved=True)
            view.update(status='cached', calibration_id=current['calibration_id'])
            self.state['quest_archive_aligned'] = view

    def apply_controller_calibration(self, samples):
        # Recompute from observations: never trust a caller's proposed transform or pass flag.
        solution = alignment.solve(*(samples[p] for p in ('pc_before', 'quest', 'pc_after')))
        pc = solution['phases']['pc_after']['identity']
        quest = solution['phases']['quest']['identity']
        if pc != self.pc_identity() or quest != self.quest_identity:
            raise ValueError('Calibration source session/generation does not match this bridge')
        if not self.pc_ready():
            raise ValueError('PC tracking is unavailable; calibration cannot be activated')
        age = int(self.state['sample_time_ns'])-int(samples['pc_after'][-1]['sample_time_ns'])
        if not 0 <= age <= 30_000_000_000:
            raise ValueError('Calibration return measurement must be from the last 30 seconds')
        value = {key: solution[key] for key in ('method', 'transform', 'yaw_deg', 'validation',
                                               'round_trip_drift', 'controller_separation_m')}
        value.update(status='valid', calibration_id=str(uuid.uuid4()), pc_identity=pc, quest_identity=quest,
                     wivrn_owner=self.state['wivrn'].get('owner'), applied_unix_ns=str(time.time_ns()))
        self.calibration = value
        self.state['alignment'] = value
        self.publish()
        return value['calibration_id']

    def publish(self):
        self.sequence += 1
        packet = encode(self.snapshot())
        for writer, queue in list(self.clients.items()):
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                writer.close()
                self.clients.pop(writer, None)

    def invalidate(self, reason):
        self.invalidate_alignment(reason)
        self.last_sample = 0
        self.state.update(status=reason, devices={}, anchors={}, boundary=None)
        self.publish()

    def begin_provider(self):
        self.provider_paused = False
        self.state.update(provider_session=str(uuid.uuid4()), generation=0, sample_time_ns=None)
        self.state.pop('provider_error', None)
        self.invalidate('connecting')

    def ingest(self, message):
        kind = message.get('type')
        if kind == 'capabilities':
            self.state['capabilities']['headless'] = message.get('headless', False)
        elif kind == 'runtime':
            self.state['runtime'] = message.get('name', '')
        elif kind == 'tracking':
            if self.provider_paused:
                return  # A surviving helper is not proof of live headset transport.
            if message.get('reference_space') != 'pc_stage':
                raise ValueError('Unsupported provider coordinate space')
            self.last_sample = time.monotonic()
            self.state.update(status='tracking', devices=message['devices'], anchors={},
                              generation=message['generation'], boundary=message.get('boundary'),
                              sample_time_ns=message['sample_time_ns'])
        elif kind == 'error':
            self.state['provider_error'] = message.get('message', 'Provider failed')
            self.invalidate('provider_error')
            return
        else:
            return
        self.publish()

    def expire(self):
        if self.last_sample and time.monotonic() - self.last_sample > self.ttl:
            self.invalidate('stale')
        if self.quest_seen and time.monotonic() - self.quest_seen > self.ttl:
            self.invalidate_quest('stale')

    def invalidate_quest(self, reason):
        self.quest_seen = 0
        self.state['quest'].update(status=reason, devices={}, anchors={}, boundary=None, scene=None)
        self.publish()

    def ingest_quest(self, data):
        if not isinstance(data, dict) or data.get('type') != 'quest_snapshot' or data.get('reference_space') != 'quest_stage':
            raise ValueError('Unsupported Quest provider packet')
        if len(encode(data)) > 24576:
            raise ValueError('Quest snapshot exceeds v1 provider budget')
        # Keep the two coordinate systems separate. This is not a PC-space anchor.
        value = {key: data[key] for key in ('source_session', 'sequence', 'generation',
                 'status', 'capabilities', 'sample_time_ns', 'xr_state', 'resumed',
                 'devices', 'anchors', 'boundary', 'scene', 'capture', 'error') if key in data}
        value.update(reference_space='quest_stage', alignment_to_pc='unresolved')
        value.setdefault('devices', {})
        value.setdefault('anchors', {})
        if not isinstance(value['devices'], dict) or not isinstance(value['anchors'], dict):
            raise ValueError('Invalid Quest poses')
        scene = value.get('scene')
        if scene is not None and (not isinstance(scene, dict) or not isinstance(scene.get('entities', []), list)
                or any(not isinstance(item, dict) or not isinstance(item.get('pose', {}), dict) for item in scene.get('entities', []))):
            raise ValueError('Invalid Quest scene')
        source = value.get('source_session')
        if source is not None:
            # Lifecycle packets omit generation. Preserve it only within the same session.
            generation = value.get('generation')
            if generation is None and self.quest_identity and self.quest_identity[1] == source:
                generation = self.quest_identity[2]
                value['generation'] = generation
            self.quest_identity = [self.session, source, generation]
        self.quest_seen = time.monotonic()
        self.state['quest'] = value
        if value.get('status') != 'tracking' or not value.get('resumed', False):
            self.invalidate_quest(value.get('status', 'unavailable'))
            return
        self.archive_quest(value)
        self.publish()

    def archive_quest(self, value):
        # Save geometry/identity, never serialize a live-valid tracking claim.
        scene = value.get('scene') or {}
        markers = {key: item for key, item in value['anchors'].items()
                   if isinstance(item, dict) and item.get('persisted') and item.get('valid')}
        key = (value.get('source_session'), value.get('generation'), tuple(sorted(markers)),
               scene.get('complete'), scene.get('query_result'),
               tuple((item.get('uuid'), item.get('pose', {}).get('valid')) for item in scene.get('entities', [])))
        has_scene = any(item.get('pose', {}).get('valid') for item in scene.get('entities', []))
        if key == self.quest_archive_key or (not markers and not has_scene):
            return
        archive = {'version': 1, 'reference_space': 'quest_stage',
                   'alignment_to_pc': 'unresolved', 'source_session': value.get('source_session'),
                   'generation': value.get('generation'),
                   'captured_unix_ns': str(time.time_ns()),
                   'boundary': value.get('boundary'), 'markers': {}, 'scene': copy.deepcopy(scene)}
        for uuid_key, marker in markers.items():
            archive['markers'][uuid_key] = {'slot': marker.get('slot'),
                'saved_pose': {field: marker.get(field) for field in ('position', 'rotation')}}
        for entity in archive['scene'].get('entities', []):
            old_pose = entity.pop('pose', {})
            entity['saved_pose'] = {field: old_pose.get(field) for field in ('position', 'rotation')}
            entity['localized_when_saved'] = old_pose.get('valid', False)
        raw = encode(archive)
        if len(raw) > 32768:
            self.state['quest_archive_error'] = 'Spatial cache exceeds 32 KiB limit'
            return
        self.state['quest_archive'] = archive
        if not self.catalog_path:
            self.quest_archive_key = key
            return
        path = Path(self.catalog_path).with_name('quest-spatial-cache.json')
        temp = path.with_name('.quest-spatial-cache.' + self.session)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(path)
            self.state['quest_archive'] = archive
            self.quest_archive_key = key
            self.state.pop('quest_archive_error', None)
        except OSError as error:
            self.state['quest_archive_error'] = str(error)
        finally:
            temp.unlink(missing_ok=True)

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        sender = None
        try:
            if len(self.handlers) > MAX_CONNECTIONS:
                return
            hello = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if not isinstance(hello, dict) or hello.get('version') != VERSION:
                return
            token = hello.get('token')
            if hello.get('op') in ('calibrate_controllers', 'clear_calibration'):
                if not isinstance(token, str) or not secrets.compare_digest(token, self.calibration_token):
                    return
                try:
                    if hello['op'] == 'clear_calibration':
                        self.invalidate_alignment('cleared_by_user')
                        self.publish()
                        result = {'accepted': True, 'calibration_id': None}
                    else:
                        samples = {}
                        for phase in ('pc_before', 'quest', 'pc_after'):
                            part = json.loads(await asyncio.wait_for(reader.readline(), 5))
                            if not isinstance(part, dict) or part.get('phase') != phase:
                                raise ValueError('Expected calibration phase '+phase)
                            samples[phase] = part['samples']
                        result = {'accepted': True, 'calibration_id': self.apply_controller_calibration(samples)}
                except (ValueError, TypeError, KeyError, IndexError, OverflowError) as error:
                    result = {'accepted': False, 'error': str(error)}
                writer.write(encode(dict(result, version=VERSION, type='calibration_result')))
                await asyncio.wait_for(writer.drain(), 2)
                return
            if hello.get('op') == 'provide_quest':
                if not isinstance(token, str) or not secrets.compare_digest(token, self.provider_token) or self.quest_writer:
                    return
                self.quest_writer = writer
                self.invalidate_quest('connecting')
                try:
                    while True:
                        line = await asyncio.wait_for(reader.readline(), 5)
                        if not line: break
                        self.ingest_quest(json.loads(line))
                finally:
                    self.quest_writer = None
                    self.invalidate_quest('disconnected')
                return
            if hello.get('op') != 'subscribe': return
            if not isinstance(token, str) or not secrets.compare_digest(token, self.token):
                return
            self.expire()
            queue = asyncio.Queue(maxsize=8)
            queue.put_nowait(encode(self.snapshot()))
            self.clients[writer] = queue
            async def send():
                while True:
                    writer.write(await queue.get())
                    await asyncio.wait_for(writer.drain(), 2)
            sender = asyncio.create_task(send())
            # Read-only protocol: EOF or any further input closes this connection.
            reader_task = asyncio.create_task(reader.read(1))
            try:
                await asyncio.wait({sender, reader_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader_task
        except (ValueError, TypeError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionError, asyncio.TimeoutError):
                    await sender
            self.clients.pop(writer, None)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.handlers.discard(task)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.invalidate('stopped')
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        endpoint = self.directory / 'endpoint.json'
        if endpoint.exists():
            try:
                if json.loads(endpoint.read_text()).get('token') == self.token:
                    endpoint.unlink()
                    (self.directory / 'quest-provider.json').unlink(missing_ok=True)
                    (self.directory / 'calibration-provider.json').unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        if self.lock:
            self.lock.close()


async def read_provider(broker, helper, runtime):
    broker.begin_provider()
    env = dict(os.environ, XR_RUNTIME_JSON=str(runtime))
    process = await asyncio.create_subprocess_exec(str(helper), env=env,
                  stdout=asyncio.subprocess.PIPE, limit=LIMIT)
    try:
        async for line in process.stdout:
            broker.ingest(json.loads(line))
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        broker.invalidate('provider_disconnected')


async def monitor(broker, track, helper, runtime):
    provider = None
    owner = None
    disconnected_since = None
    try:
        while True:
            try:
                status = await installed_status()
            except (OSError, asyncio.TimeoutError):
                status = {'available': False, 'headset_connected': False, 'session_running': False}
            ready = status['headset_connected'] and status['session_running']
            if ready:
                disconnected_since = None
            elif provider and disconnected_since is None:
                disconnected_since = time.monotonic()
            if provider and not ready and not broker.provider_paused:
                broker.provider_paused = True
                broker.invalidate('disconnected')
            expired = disconnected_since is not None and time.monotonic()-disconnected_since >= RECONNECT_GRACE
            if provider and (provider.done() or expired or status.get('owner') != owner):
                provider.cancel()
                await asyncio.gather(provider, return_exceptions=True)
                provider = None
                broker.invalidate('disconnected')
            if ready:
                broker.provider_paused = False
            if status != broker.state['wivrn']:
                broker.state['wivrn'] = status
                broker.publish()
            if track and ready and provider is None:
                owner = status.get('owner')
                provider = asyncio.create_task(read_provider(broker, helper, runtime))
            await asyncio.sleep(2)
    finally:
        if provider:
            provider.cancel()
            await asyncio.gather(provider, return_exceptions=True)


async def run(args):
    broker = Broker(args.directory or default_directory(), catalog_path=args.catalog)
    tasks = []
    try:
        await broker.start()
        print(f'Room bridge ready: {broker.directory / "endpoint.json"}', flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        async def expiry():
            ticks = 0
            while True:
                await asyncio.sleep(0.1)
                broker.expire()
                ticks += 1
                if ticks % 5 == 0:
                    broker.refresh_catalog()
                    broker.publish()  # Heartbeat also while WiVRn is stopped.
        tasks = [asyncio.create_task(monitor(broker, args.track, args.helper, args.runtime)),
                 asyncio.create_task(expiry())]
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await broker.close()


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--track', action='store_true', help='Attach a read-only headless OpenXR session')
    parser.add_argument('--catalog', type=Path, default=catalog.default_path())
    parser.add_argument('--helper', type=Path, default=root / 'build/tracking-source')
    parser.add_argument('--runtime', type=Path, default=Path('/usr/share/openxr/1/openxr_wivrn.json'))
    args = parser.parse_args()
    if args.track and (not args.helper.is_file() or not args.runtime.is_file()):
        parser.error('Build tracking-source and select the installed WiVRn runtime manifest')
    asyncio.run(run(args))
