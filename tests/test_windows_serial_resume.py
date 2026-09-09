import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from neurobridge.adapters.sources.serial_resume import WindowsHeadsetResume
from neurobridge.serial.adapter import SerialAdapter, HANDSHAKE, START_COMMAND, STOP_COMMAND
from neurobridge.config import SerialConfig
from neurobridge.config import load
from neurobridge.bootstrap import build_container
from neurobridge.profiles.resolver import RuntimePlatform
from neurobridge.domain.algorithm import AlgorithmState
from test_serial import FakeSerial, frame, noop


class WindowsResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'resume.json'
        self.probe_ready = True
        self.probe_active = False
        self.probe_count = 0

    def policy(self):
        @asynccontextmanager
        async def algorithm():
            self.probe_count += 1
            self.probe_active = self.probe_ready
            try:
                yield self.probe_ready
            finally:
                self.probe_active = False
        return WindowsHeadsetResume(self.path, lambda p: {'port': p, 'vid': '10c4', 'pid': 'ea60'}, algorithm)

    async def run_device(self, client, policy=None):
        states, packets = [], []
        async def receive(packet):
            packets.append(packet)
            if packet.channel == 'ff31':
                await adapter.stop()
        async def status(name, value):
            states.append(value)
        async def ready():
            self.assertIn('validated', states)
            self.assertFalse(self.probe_active)
            return True
        async def failed(reason):
            await adapter.stop()
        adapter = SerialAdapter(SerialConfig(handshake_timeout_ms=15, command_response_timeout_ms=25,
            data_timeout_seconds=0.1), receive, status, ready, failed,
            candidate_provider=lambda c: ['COM4'], serial_factory=lambda p,c: client,
            identity_provider=lambda p: dict.fromkeys(('resolvedPath','vid','pid','usbSerial','usbParent','interface','driver','physicalPath'),p),
            restart_probe=policy or self.policy())
        await asyncio.wait_for(adapter.run(), 3)
        self.assertTrue(client.closed)
        self.assertFalse(self.probe_active)
        return states, packets

    def device(self, *, ack=True, e1=True, first_e1_silent=False):
        owner = self
        class Device(FakeSerial):
            def __init__(self):
                super().__init__([])
                self.starts = 0
            def write(self, value):
                super().write(value)
                if value == HANDSHAKE and ack:
                    self.reads.append(b'\x01')
                if value == START_COMMAND:
                    self.starts += 1
                    # Before validation, E1 may only run inside a ready probe.
                    if HANDSHAKE not in self.writes or not ack:
                        owner.assertTrue(owner.probe_active)
                    if e1 and (not first_e1_silent or self.starts > 1):
                        self.reads.append(frame(7, 4, 63))
                return len(value)
        return Device()

    async def test_normal_stop_then_new_process_resumes_without_ack(self):
        first = self.device()
        await self.run_device(first)
        self.assertEqual(first.writes, [HANDSHAKE, START_COMMAND, STOP_COMMAND])
        self.assertTrue(self.policy().prefer_resume('COM4'))
        second = self.device(ack=False)
        states, packets = await self.run_device(second, self.policy())
        self.assertEqual(second.writes, [START_COMMAND, STOP_COMMAND])
        self.assertIn('validated', states)
        self.assertTrue(any(p.channel == 'serial.frame' for p in packets))

    async def test_resume_silence_falls_back_to_ack_then_normal_start(self):
        self.policy().command_sent('stop', 'COM4')
        client = self.device(first_e1_silent=True)
        await self.run_device(client)
        self.assertEqual(client.writes, [START_COMMAND, HANDSHAKE, START_COMMAND, STOP_COMMAND])

    async def test_no_hint_ack_timeout_falls_back_to_e1(self):
        client = self.device(ack=False)
        await self.run_device(client)
        self.assertEqual(client.writes, [HANDSHAKE, START_COMMAND, STOP_COMMAND])

    async def test_invalid_data_does_not_validate_and_failed_probe_stops_capture(self):
        client = self.device(ack=False, e1=False)
        original = client.write
        def noise(value):
            count = original(value)
            if value == START_COMMAND:
                client.reads.append(b'\x01garbage')
            return count
        client.write = noise
        states, packets = await self.run_device(client)
        self.assertNotIn('validated', states)
        self.assertFalse(packets)
        self.assertEqual(client.writes, [HANDSHAKE, START_COMMAND, STOP_COMMAND])
        self.assertFalse(self.path.exists())

    async def test_algorithm_failure_prevents_e1(self):
        self.probe_ready = False
        client = self.device(ack=False)
        states, packets = await self.run_device(client)
        self.assertEqual(client.writes, [HANDSHAKE])
        self.assertNotIn('validated', states)
        self.assertFalse(packets)

    async def test_existing_valid_stream_sends_neither_ack_nor_e1(self):
        client = FakeSerial([frame(7, 4, 63)])
        await self.run_device(client)
        self.assertEqual(client.writes, [STOP_COMMAND])
        self.assertEqual(self.probe_count, 0)

    async def test_hint_is_scoped_to_device_and_corruption_falls_back(self):
        policy = self.policy()
        policy.command_sent('stop', 'COM4')
        self.assertFalse(policy.prefer_resume('COM5'))
        self.path.write_text('{bad json')
        self.assertFalse(policy.prefer_resume('COM4'))

    async def test_cancelled_e1_probe_stops_capture_and_closes_algorithm(self):
        client = self.device(ack=False, e1=False)
        entered = asyncio.Event()
        async def observe(client, path):
            entered.set()
            await asyncio.Future()
        async def ack(client):
            return b''
        task = asyncio.create_task(self.policy().probe(client, 'COM4', observe, ack, lambda: False))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(client.writes, [START_COMMAND, STOP_COMMAND])
        self.assertFalse(self.probe_active)

    async def test_production_windows_profile_wires_isolated_algorithm_and_resume_state(self):
        instances = []
        class Engine:
            def __init__(self, config):
                instances.append(self)
                self.closed = False
            async def initialize(self, session):
                return AlgorithmState.READY
            async def close(self):
                self.closed = True
        config = load(Path(__file__).resolve().parents[1] / 'windows/gateway.toml.example')
        config = replace(config, recording=replace(config.recording, directory=Path(self.directory.name)))
        with patch('neurobridge.bootstrap.container.AffectiveSdkAlgorithmEngine', Engine):
            container = build_container(config, RuntimePlatform('windows', 'x86_64'))
            policy = container.source._adapter.restart_probe
            self.assertEqual(policy.state_path, Path(self.directory.name) / '.windows-serial-resume.json')
            async with policy.algorithm_context() as ready:
                self.assertTrue(ready)
                self.assertEqual(len(instances), 2)
                self.assertFalse(instances[-1].closed)
                self.assertIsNone(container.gateway.store.recording_id)
            self.assertTrue(instances[-1].closed)
            self.assertFalse(instances[0].closed)
