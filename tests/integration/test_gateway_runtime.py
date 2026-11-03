"""Regressions for runtime supervision, pure status and cross-entry restart."""
import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from neurobridge.application.gateway import ClientSession
from neurobridge.adapters.storage.archive import ArchiveReplayReader
from neurobridge.bootstrap import build_container, build_runtime
from neurobridge.config import load
from neurobridge.profiles.resolver import RuntimePlatform
from neurobridge.runtime import GatewayRuntime

ROOT = Path(__file__).resolve().parents[2]


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        cfg = load(ROOT / 'config/gateway.ubuntu.toml.example')
        self.config = replace(cfg, recording=replace(cfg.recording, directory=Path(self.directory.name), replay_enabled=False),
                              algorithm=replace(cfg.algorithm, enabled=False))
        self.container = build_container(self.config, RuntimePlatform('ubuntu', 'x86_64'))
        self.gateway = self.container.gateway
        await self.gateway.start()
        self.runtime = None

    async def asyncTearDown(self):
        if self.runtime:
            await self.runtime.close()
        await self.gateway.stop()
        self.directory.cleanup()

    async def test_producer_exception_is_observable_and_recovers_with_fresh_session(self):
        sessions = []
        async def healthy():
            await asyncio.Future()
        def factory():
            c = self.container if not sessions else build_container(self.config, RuntimePlatform('ubuntu', 'x86_64'), gateway=self.gateway)
            c.source._adapter.run = AsyncMock(side_effect=RuntimeError('producer disappeared') if not sessions else healthy)
            sessions.append(c)
            return c.device_adapter
        self.runtime = GatewayRuntime(self.gateway, factory, restart_delay_seconds=.05)
        await self.runtime.start()
        await eventually(lambda: self.gateway.connection_error is not None)
        self.assertIn('producer disappeared', self.gateway.connection_error)
        self.assertFalse(self.runtime.running)
        await eventually(lambda: len(sessions) == 2 and self.runtime.running)
        self.assertIsNot(sessions[0].application, sessions[1].application)
        self.assertTrue(sessions[0].application._closed)
        self.assertFalse(sessions[1].application._closed)
        self.assertIsNotNone(self.gateway.recording_repository)
        await self.runtime.stop()
        self.assertFalse(self.runtime.running)
        count = len(sessions)
        await asyncio.sleep(.07)
        self.assertEqual(len(sessions), count)

    async def test_normal_producer_exit_is_recovered_instead_of_reported_running(self):
        sessions = []
        async def healthy():
            await asyncio.Future()
        def factory():
            c = self.container if not sessions else build_container(self.config, RuntimePlatform('ubuntu', 'x86_64'), gateway=self.gateway)
            c.source._adapter.run = AsyncMock(return_value=None) if not sessions else healthy
            sessions.append(c)
            return c.device_adapter
        self.runtime = GatewayRuntime(self.gateway, factory, restart_delay_seconds=.01)
        await self.runtime.start()
        await eventually(lambda: len(sessions) == 2 and self.runtime.running)
        self.assertIn('exited unexpectedly', self.gateway.connection_error)

    async def test_unexpected_producer_cancellation_is_not_silently_ignored(self):
        async def healthy():
            await asyncio.Future()
        sessions = []
        def factory():
            c = self.container if not sessions else build_container(self.config, RuntimePlatform('ubuntu', 'x86_64'), gateway=self.gateway)
            c.source._adapter.run = healthy
            sessions.append(c)
            return c.device_adapter
        self.runtime = GatewayRuntime(self.gateway, factory, restart_delay_seconds=.01)
        await self.runtime.start()
        await eventually(lambda: self.container.source._task is not None)
        self.container.source._task.cancel()
        await eventually(lambda: len(sessions) == 2 and self.runtime.running)
        self.assertIn('cancelled unexpectedly', self.gateway.connection_error)

    async def test_stop_during_recovery_does_not_restart_or_leave_tasks(self):
        self.container.source._adapter.run = AsyncMock(side_effect=OSError('producer failed'))
        factory = lambda: self.container.device_adapter
        self.runtime = GatewayRuntime(self.gateway, factory, restart_delay_seconds=10)
        await self.runtime.start()
        await eventually(lambda: self.gateway.connection_error is not None)
        await self.runtime.stop()
        self.assertIsNone(self.runtime.task)
        self.assertIsNone(self.runtime.session)
        self.assertIsNone(self.runtime.session_task)
        self.assertEqual(self.gateway.status['connectionState'], 'disconnected')

    async def test_cleanup_failure_blocks_replacement_until_explicit_stop_succeeds(self):
        session = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError('failed')),
                                  stop=AsyncMock(side_effect=OSError('cannot stop')))
        created = []
        def make():
            created.append(session)
            return session
        self.runtime = GatewayRuntime(self.gateway, make, restart_delay_seconds=0)
        await self.runtime.start()
        await eventually(lambda: self.runtime.task.done())
        self.assertEqual(len(created), 1)
        self.assertFalse(self.runtime.running)
        with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            await self.runtime.start()
        session.stop.side_effect = None
        await self.runtime.stop()
        self.assertIsNone(self.runtime.session)

    async def test_mac_stop_start_rebuilds_application_and_preserves_listener(self):
        spec = importlib.util.spec_from_file_location('runtime_mac_poc', ROOT / 'mac/poc_server.py')
        poc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(poc)
        config = replace(self.config, profile='macos_headband_wired')
        c = build_container(config, RuntimePlatform('macos', 'aarch64'))
        supervisor = build_runtime(c, RuntimePlatform('macos', 'aarch64'))
        server = SimpleNamespace(close=unittest.mock.Mock(), wait_closed=AsyncMock())
        async def healthy(_adapter):
            await asyncio.Future()
        with patch.object(poc, 'build_container', return_value=c), patch.object(poc, 'build_runtime', return_value=supervisor), patch.object(poc, 'create_server', AsyncMock(return_value=server)) as create, patch('neurobridge.ble.flowtime.FlowtimeAdapter.run', healthy):
            controller = poc.CaptureController(config)
            try:
                await controller.start()
                await eventually(lambda: supervisor.session.source._task is not None)
                first = supervisor.session
                await controller.stop()
                self.assertFalse(controller.snapshot()['captureRunning'])
                self.assertTrue(first.application._closed)
                server.close.assert_not_called()
                await controller.start()
                await eventually(lambda: supervisor.session.source._task is not None)
                self.assertTrue(controller.snapshot()['captureRunning'])
                self.assertIsNot(supervisor.session, first)
                self.assertFalse(supervisor.session.application._closed)
                self.assertIs(supervisor.session.application.recording, controller.gateway.recording_repository)
                self.assertEqual(create.await_count, 1)
            finally:
                await controller.shutdown()
            server.close.assert_called_once()


class CatalogueTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_status_and_status_subscription_do_not_wait_for_slow_catalogue(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(ROOT / 'config/gateway.ubuntu.toml.example')
            config = replace(config, recording=replace(config.recording, directory=Path(directory)),
                             pipeline=replace(config.pipeline, replay_catalog_timeout_ms=30))
            c = build_container(config, RuntimePlatform('ubuntu', 'x86_64'))
            gate = asyncio.Event()
            async def slow(_):
                await gate.wait()
                return None, frozenset(), None, None
            c.gateway.replay_reader.inspect = AsyncMock(side_effect=slow)
            await c.gateway.start()
            try:
                await asyncio.sleep(0)
                for action, params in [('getStatus', {}), ('subscribe', {'streams':['status']})]:
                    messages = []
                    async def send(message): messages.append(message)
                    await asyncio.wait_for(c.northbound_controller.handle(ClientSession(), json.dumps({'protocolVersion':'1.0','messageType':'request','requestId':action,'action':action,'params':params}), send), .1)
                    self.assertEqual(messages[0]['code'], 200)
                messages = []
                async def send(message): messages.append(message)
                await asyncio.wait_for(c.northbound_controller.handle(ClientSession(), json.dumps({'protocolVersion':'1.0','messageType':'request','requestId':'latest','action':'getLatest','params':{'streams':['eeg']}}), send), .2)
                self.assertEqual(messages[0]['code'], 503)
                self.assertEqual(c.gateway.status_result()['connectionState'], 'disconnected')
            finally:
                await c.gateway.stop()

    async def test_catalogue_caches_and_invalidates_without_returning_stale_generation(self):
        archive = SimpleNamespace()
        reader = ArchiveReplayReader(archive, cache_ttl_seconds=30)
        first_entered, release = Event(), Event()
        calls = []
        def scan(_):
            calls.append(len(calls))
            if len(calls) == 1:
                first_entered.set()
                release.wait(2)
                return 'old', frozenset(), None, None
            return 'new', frozenset({'eeg'}), {'attention':7}, 1
        reader._inspect = scan
        task = asyncio.create_task(reader.inspect(None))
        try:
            await eventually(first_entered.is_set)
            reader.invalidate()
            release.set()
            self.assertEqual((await asyncio.wait_for(task, 1))[0], 'new')
            self.assertEqual((await reader.inspect(None))[0], 'new')
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
            await reader.close()

    async def test_timed_out_calls_share_one_physical_scan(self):
        reader = ArchiveReplayReader(SimpleNamespace())
        entered, release = Event(), Event()
        calls = []
        def scan(_):
            calls.append(1)
            entered.set()
            release.wait(2)
            return None, frozenset(), None, None
        reader._inspect = scan
        try:
            for _ in range(3):
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(reader.inspect(None), .03)
            self.assertEqual(len(calls), 1)
            release.set()
            await reader.inspect(None)
        finally:
            release.set()
            await reader.close()

    async def test_cancelled_catalogue_scan_is_not_duplicated_by_concurrent_retries(self):
        reader = ArchiveReplayReader(SimpleNamespace())
        entered, release = Event(), Event()
        calls = []
        def scan(_):
            calls.append(1)
            entered.set()
            release.wait(2)
            return 'saved', frozenset({'eeg'}), None, None
        reader._inspect = scan
        first = asyncio.create_task(reader.inspect(None))
        retries = []
        try:
            await eventually(entered.is_set)
            await reader.close()
            await asyncio.gather(first, return_exceptions=True)
            retries = [asyncio.create_task(reader.inspect(None)) for _ in range(3)]
            await asyncio.sleep(.03)
            self.assertEqual(len(calls), 1)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*retries), 1)
            self.assertTrue(all(result[0] == 'saved' for result in results))
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
            for task in retries:
                task.cancel()
            await asyncio.gather(*retries, return_exceptions=True)
            await reader.close()
