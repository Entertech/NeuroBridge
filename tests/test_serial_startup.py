"""Stateful headset startup tests shared by Windows and Kylin."""
import asyncio
import json
import base64
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from neurobridge.serial.adapter import SerialAdapter, START_COMMAND, STOP_COMMAND
from neurobridge.config import SerialConfig, load
from neurobridge.bootstrap import build_container
from neurobridge.profiles.resolver import RuntimePlatform
from neurobridge.domain.algorithm import AlgorithmState, AlgorithmResult
from neurobridge.domain.status import ConnectionState
from neurobridge.adapters.algorithms import AffectiveSdkAlgorithmEngine
from test_serial import FakeSerial, frame, noop, ready, HANDSHAKE

ROOT = Path(__file__).resolve().parents[1]


class Headset:
    """Device state survives host port close/reopen; E1 needs no ACK."""
    def __init__(self):
        self.streaming = False
        self.starts = 0
        self.ports = []

    def open(self, *_args):
        device = self
        class Port(FakeSerial):
            def __init__(self):
                super().__init__([frame(9)] if device.streaming else [])
            def write(self, value):
                if value == HANDSHAKE:
                    raise AssertionError('ACK is forbidden even on first power-on')
                result = super().write(value)
                if value == START_COMMAND:
                    device.starts += 1
                    device.streaming = True
                    self.reads.append(frame(device.starts))
                elif value == STOP_COMMAND:
                    device.streaming = False
                return result
        port = Port()
        self.ports.append(port)
        return port


class SerialStartupTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, client, *, prepare=ready, on_ready=ready, raw_chunk=None, paths=None, factory=None):
        states, packets, errors = [], [], []
        async def packet(value):
            packets.append(value)
            if value.channel == 'ff31':
                await adapter.stop()
        async def status(name, value):
            states.append(value)
        async def error(reason):
            errors.append(reason)
            await adapter.stop()
        adapter = SerialAdapter(
            SerialConfig(handshake_timeout_ms=10, data_timeout_seconds=0.15, reconnect_delay_seconds=0.01),
            packet, status, on_ready, error, prepare_algorithm=prepare,
            candidate_provider=lambda _: paths or ['COM4'],
            serial_factory=factory or (lambda *_: client), raw_chunk=raw_chunk,
        )
        return adapter, states, packets, errors

    async def test_cold_boot_and_host_restarts_never_require_ack_or_saved_identity(self):
        headset = Headset()
        for _ in range(3):
            client = headset.open()
            adapter, states, packets, errors = self.adapter(client)
            await asyncio.wait_for(adapter.run(), 3)
            self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])
            self.assertIn('validated', states)
            self.assertTrue(packets)
            self.assertFalse(errors)
            self.assertTrue(client.closed)
            self.assertFalse(headset.streaming)
        self.assertEqual(headset.starts, 3)

    async def test_discovery_and_algorithm_prepare_overlap_and_e1_waits_for_both(self):
        discovery_started, allow_discovery = threading.Event(), threading.Event()
        preparation_started, allow_preparation = asyncio.Event(), asyncio.Event()
        client = Headset().open()
        async def prepare():
            preparation_started.set()
            await allow_preparation.wait()
            return True
        adapter, states, *_ = self.adapter(client, prepare=prepare)
        def discover(_):
            discovery_started.set()
            if not allow_discovery.wait(2):
                raise RuntimeError('test discovery stalled')
            return ['COM4']
        adapter.candidate_provider = discover
        task = asyncio.create_task(adapter.run())
        try:
            await asyncio.wait_for(preparation_started.wait(), 1)
            self.assertTrue(await asyncio.to_thread(discovery_started.wait, 1))
            allow_discovery.set()
            while 'validating' not in states:
                await asyncio.sleep(0.001)
            self.assertEqual(client.writes, [])
            allow_preparation.set()
            await asyncio.wait_for(task, 3)
        finally:
            allow_discovery.set()
            allow_preparation.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])

    async def test_algorithm_ready_does_not_allow_e1_before_open_finishes(self):
        opening, release = threading.Event(), threading.Event()
        prepared = asyncio.Event()
        client = Headset().open()
        async def prepare():
            prepared.set()
            return True
        def factory(*_):
            opening.set()
            release.wait(2)
            return client
        adapter, *_ = self.adapter(client, prepare=prepare, factory=factory)
        task = asyncio.create_task(adapter.run())
        try:
            await asyncio.wait_for(prepared.wait(), 1)
            self.assertTrue(await asyncio.to_thread(opening.wait, 1))
            self.assertEqual(client.writes, [])
            release.set()
            await asyncio.wait_for(task, 3)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_e1_write_and_01_or_noise_do_not_validate(self):
        for payload in (b'', b'\x01', b'noise\x01noise', frame(1)[:-3] + b'bad'):
            with self.subTest(payload=payload):
                class Port(FakeSerial):
                    def write(self, value):
                        n = super().write(value)
                        if value == START_COMMAND:
                            self.reads.append(payload)
                        return n
                client = Port([])
                async def unexpected_session():
                    self.fail('Cannot create a session without a valid frame')
                adapter, states, packets, errors = self.adapter(client, on_ready=unexpected_session)
                await asyncio.wait_for(adapter.run(), 3)
                self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])
                self.assertNotIn('validated', states)
                self.assertEqual(states[-1], 'validation_failed')
                self.assertFalse(packets)
                self.assertIn('no valid 28-byte frame', errors[0])

    async def test_algorithm_error_prevents_e1_and_session_creation(self):
        async def failure():
            raise RuntimeError('algorithm failed')
        client = Headset().open()
        adapter, states, packets, errors = self.adapter(client, prepare=failure)
        await asyncio.wait_for(adapter.run(), 3)
        self.assertEqual(client.writes, [])
        self.assertNotIn('validated', states)
        self.assertFalse(packets)
        self.assertIn('algorithm is not ready', errors[0])

    async def test_existing_stream_adopted_without_e1_even_when_algorithm_unavailable(self):
        headset = Headset()
        headset.streaming = True
        client = headset.open()
        async def unavailable():
            return False
        adapter, states, packets, _ = self.adapter(client, prepare=unavailable, on_ready=unavailable)
        await asyncio.wait_for(adapter.run(), 3)
        self.assertIn('validated', states)
        self.assertTrue(packets)
        self.assertEqual(client.writes, [STOP_COMMAND])

    async def test_multicandidate_failed_probe_e0_before_next_candidate(self):
        silent, working = FakeSerial([]), Headset().open()
        def factory(path, _):
            if path == 'COM5':
                self.assertTrue(silent.closed)
                self.assertEqual(silent.writes, [START_COMMAND, STOP_COMMAND])
            return silent if path == 'COM4' else working
        adapter, states, packets, errors = self.adapter(None, paths=['COM4', 'COM5'], factory=factory)
        await asyncio.wait_for(adapter.run(), 3)
        self.assertIn('validated', states)
        self.assertFalse(errors)
        self.assertTrue(packets)
        self.assertEqual(working.writes, [START_COMMAND, STOP_COMMAND])

    async def test_cancel_open_closes_late_handle_and_releases_preparation(self):
        entered, release = threading.Event(), threading.Event()
        cleaned = []
        client = FakeSerial([])
        def opening(*_):
            entered.set()
            release.wait(2)
            return client
        adapter, *_ = self.adapter(client, factory=opening)
        async def cleanup():
            cleaned.append(True)
        adapter.release_algorithm = cleanup
        task = asyncio.create_task(adapter.run())
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(client.closed)
        self.assertEqual(client.writes, [])
        self.assertEqual(cleaned, [True])

    async def test_cancel_after_e1_stops_unvalidated_device(self):
        started = threading.Event()
        class Port(FakeSerial):
            def write(self, value):
                n = super().write(value)
                if value == START_COMMAND:
                    started.set()
                return n
        client = Port([])
        adapter, states, *_ = self.adapter(client)
        task = asyncio.create_task(adapter.run())
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])
        self.assertNotIn('validated', states)
        self.assertTrue(client.closed)

    async def test_cancel_while_waiting_for_algorithm_never_starts_device(self):
        entered, released = asyncio.Event(), []
        async def preparing():
            entered.set()
            await asyncio.Future()
        async def release():
            released.append(True)
        client = FakeSerial([])
        adapter, states, *_ = self.adapter(client, prepare=preparing)
        adapter.release_algorithm = release
        task = asyncio.create_task(adapter.run())
        await asyncio.wait_for(entered.wait(), 1)
        async with asyncio.timeout(1):
            while 'validating' not in states:
                await asyncio.sleep(0.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(client.writes, [])
        self.assertTrue(client.closed)
        self.assertEqual(released, [True])

    async def test_stream_arrives_while_preparing_and_is_adopted_without_e1(self):
        client = FakeSerial([])
        async def prepare():
            async with asyncio.timeout(1):
                while 'validating' not in states:
                    await asyncio.sleep(0.001)
            client.reads.append(frame(5))
            return True
        adapter, states, packets, errors = self.adapter(client, prepare=prepare)
        await asyncio.wait_for(adapter.run(), 3)
        self.assertEqual(client.writes, [STOP_COMMAND])
        self.assertTrue(packets)
        self.assertFalse(errors)

    async def test_cancel_after_validation_before_control_adoption_still_sends_e0(self):
        client = Headset().open()
        validated = asyncio.Event()
        async def status(name, value):
            if value == 'validated':
                validated.set()
                await asyncio.Future()
        async def external_stop():
            pass  # Session control has not adopted the new handle yet.
        adapter, *_ = self.adapter(client)
        adapter.status = status
        adapter.external_control = True
        adapter.external_stop = external_stop
        task = asyncio.create_task(adapter.run())
        await asyncio.wait_for(validated.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])
        self.assertTrue(client.closed)

    async def test_probe_preserves_fragment_boundaries_and_timestamps(self):
        data = frame(42)
        class Port(FakeSerial):
            def write(self, value):
                n = super().write(value)
                if value == START_COMMAND:
                    self.reads.extend([data[:8], data[8:]])
                return n
        client, reads = Port([]), []
        async def raw(value, stamp):
            reads.append((value, stamp))
            if len(reads) == 2:
                await adapter.stop()
        adapter, states, *_ = self.adapter(client, raw_chunk=raw)
        with patch('neurobridge.serial.adapter.wall_clock_ms', side_effect=[101, 102]):
            await asyncio.wait_for(adapter.run(), 3)
        self.assertEqual(reads, [(data[:8], 101), (data[8:], 102)])
        self.assertIn('validated', states)


class Engine:
    """Fake SDK with one resource per initialization and explicit transfer."""
    instances = []
    def __init__(self, _config):
        self.resource = None
        self.initializations = 0
        self.inputs = []
        Engine.instances.append(self)
    @property
    def available(self):
        return self.resource is not None
    async def initialize(self, session):
        self.initializations += 1
        self.resource = object()
        return AlgorithmState.READY
    async def adopt_prepared(self, prepared):
        await self.close()
        self.resource, prepared.resource = prepared.resource, None
    async def evaluate(self, value):
        self.inputs.append(value)
        return AlgorithmResult(value.batch_id, 'test', 1, 2, {})
    async def close(self):
        self.resource = None
    stop = close


class SerialProductionStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_start_with_history_creates_no_recording_or_replay(self):
        from neurobridge.business.recording import RecordingStore
        from neurobridge.application.gateway import ClientSession, ProtocolError
        for os_name, template in [('kylin', 'config/gateway.project.toml.example'),
                                  ('windows', 'windows/gateway.toml.example')]:
            with self.subTest(os_name=os_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                history = RecordingStore(root)
                old_session = history.start(1000)
                history.save_algorithm(timestamp_ms=1000, valid=True, invalid_reasons=[], algorithm={'attention': 1})
                history.stop()
                config = load(ROOT / template)
                config = replace(config, recording=replace(config.recording, directory=root),
                    serial=replace(config.serial, handshake_timeout_ms=10, data_timeout_seconds=0.15))
                with patch('neurobridge.bootstrap.container.AffectiveSdkAlgorithmEngine', Engine):
                    container = build_container(config, RuntimePlatform(os_name, 'x86_64'))
                client = FakeSerial([])
                source, gateway = container.source, container.gateway
                source._adapter.candidate_provider = lambda _: ['test-port']
                source._adapter.serial_factory = lambda *_: client
                async def failed(reason):
                    await gateway.update_connection_error(reason)
                    await source._adapter.stop()
                source._adapter.error = failed
                await gateway.start()
                with patch.object(gateway.store, 'start', wraps=gateway.store.start) as start_recording, \
                     patch.object(gateway.store, 'events', side_effect=AssertionError('history read')):
                    task = asyncio.create_task(container.device_adapter.run())
                    try:
                        async with asyncio.timeout(3):
                            while gateway.status['connectionState'] != 'validation_failed':
                                await asyncio.sleep(0.005)
                        self.assertIsNone(gateway.store.recording_id)
                        start_recording.assert_not_called()
                        self.assertEqual(container.application.diagnostics['frames'], 0)
                        await gateway.prepare_query()
                        with self.assertRaises(ProtocolError) as error:
                            gateway.get_latest(ClientSession(), {'streams': ['eeg']})
                        self.assertEqual(error.exception.code, 409)
                        self.assertIsNone(gateway._replay_task)
                        self.assertEqual(gateway.mode(), 'live')
                        self.assertTrue((root / 'sessions' / old_session).exists())
                    finally:
                        await container.device_adapter.stop()
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        await gateway.stop()
                self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])
                self.assertTrue(client.closed)

    async def test_both_profiles_reuse_prepared_algorithm_and_persist_first_frame(self):
        for os_name, template in [('kylin', 'config/gateway.project.toml.example'),
                                  ('windows', 'windows/gateway.toml.example')]:
            with self.subTest(os_name=os_name), tempfile.TemporaryDirectory() as directory:
                Engine.instances = []
                config = load(ROOT / template)
                config = replace(config, recording=replace(config.recording, directory=Path(directory)),
                    serial=replace(config.serial, handshake_timeout_ms=10, data_timeout_seconds=1))
                with patch('neurobridge.bootstrap.container.AffectiveSdkAlgorithmEngine', Engine):
                    container = build_container(config, RuntimePlatform(os_name, 'x86_64'))
                headset = Headset()
                source = container.source
                source._adapter.candidate_provider = lambda _: ['test-port']
                source._adapter.serial_factory = headset.open
                await container.gateway.start()
                task = asyncio.create_task(container.device_adapter.run())
                try:
                    async with asyncio.timeout(2):
                        while container.application.diagnostics['frames'] < 1:
                            await asyncio.sleep(0.005)
                    self.assertEqual(source.status().state, ConnectionState.CONNECTED)
                    self.assertIsNotNone(container.gateway.store.recording_id)
                    self.assertTrue(container.gateway.algorithm.available)
                    self.assertEqual(sum(x.initializations for x in Engine.instances), 1)
                    self.assertEqual(headset.ports[0].writes, [START_COMMAND])
                    self.assertFalse((Path(directory) / '.windows-serial-resume.json').exists())
                    session = container.gateway.store.recording_id
                    await container.device_adapter.stop()
                    await container.gateway.recording_repository.close_session(session)
                    paths = list(Path(directory).glob(f"**/{session}/raw/*.jsonl"))
                    raw = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
                    self.assertTrue(raw)
                    frames = [row for row in raw if row["recordType"] == "raw.device_frame"]
                    self.assertEqual(len(frames), 1)
                    self.assertEqual(base64.b64decode(frames[0]["payload"]["rawBytes"]["bytesBase64"]), frame(1))
                finally:
                    await container.device_adapter.stop()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await container.gateway.stop()
                self.assertEqual(headset.ports[0].writes, [START_COMMAND, STOP_COMMAND])
                self.assertFalse(any(x.available for x in Engine.instances))


class PreparedSdkTransferTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_engine_transfer_does_not_close_or_reinitialize_warmed_runner(self):
        from neurobridge.config import AlgorithmConfig
        class Runner:
            available = True
            def __init__(self):
                self.stops = 0
            async def stop(self):
                self.stops += 1
        active = AffectiveSdkAlgorithmEngine(AlgorithmConfig(enabled=False, command=()))
        prepared = AffectiveSdkAlgorithmEngine(AlgorithmConfig(enabled=False, command=()))
        old, warmed = Runner(), Runner()
        active._runner, prepared._runner = old, warmed
        await active.adopt_prepared(prepared)
        await prepared.close()
        self.assertIs(active._runner, warmed)
        self.assertEqual(warmed.stops, 0)
        await active.close()
        self.assertEqual(warmed.stops, 1)
