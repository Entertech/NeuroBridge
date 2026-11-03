from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bleak import BleakClient
from neurobridge.algorithm.runner import AlgorithmRunner
from neurobridge.ble.flowtime import FlowtimeAdapter, FF31, FF32
from neurobridge.ble.link_monitor import DisconnectFilter, monitor
from neurobridge.ble.packets import DataWindow, RawPacket
from neurobridge.business.gateway import Gateway
from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, RecordingConfig, ServerConfig, load


def window(end):
    result = DataWindow(end - 600, end)
    result.append(RawPacket('ff31', end - 1, b'e' * 20))
    return result


def config(root):
    return GatewayConfig(ServerConfig('127.0.0.1', 8765, '/ws'),
                         BleConfig(True, None, 'profile', 1, 0),
                         RecordingConfig(root, None, None, 1), AlgorithmConfig(False, ()))


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pinned_bleak_backend_disconnect_and_stale_callback(self):
        class Backend:
            def __init__(self, _, disconnected_callback, **kwargs): self.callback = disconnected_callback
        a = FlowtimeAdapter(config(Path('/tmp')).ble, AsyncMock(), AsyncMock(), AsyncMock())
        old = BleakClient('00:00:00:00:00:00', disconnected_callback=a._on_disconnected, backend=Backend)
        a._client = old
        with self.assertLogs('neurobridge.ble.flowtime', level='WARNING') as logs:
            old._backend.callback(old._backend)
        self.assertTrue(a._disconnected.is_set())
        self.assertIn('detectionSource=callback', '\n'.join(logs.output))
        a._disconnected = asyncio.Event()
        a._client = BleakClient('00:00:00:00:00:00', disconnected_callback=a._on_disconnected, backend=Backend)
        old._backend.callback(old._backend)
        self.assertFalse(a._disconnected.is_set())

    async def test_first_notifications_persist_before_connected_and_failure_closes_session(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                g = Gateway(config(root))
                await g.start()
                connected, ended = asyncio.Event(), asyncio.Event()
                a = FlowtimeAdapter(g.config.ble, g.receive_packet, g.update_status, g.on_device_ready)
                ids = []
                original_status = a.status
                async def status(name, value):
                    await original_status(name, value)
                    if value == 'connected': connected.set()
                    if value == 'disconnected':
                        if fail: a._stopping = True
                        ended.set()
                a.status = status
                class Client:
                    def __init__(self, _, disconnected_callback):
                        self.is_connected = False
                        self.callback = disconnected_callback
                        self.handlers = {}
                        self.services = SimpleNamespace(get_characteristic=lambda _: None)
                    async def connect(self): self.is_connected = True
                    async def start_notify(self, c, handler):
                        self.handlers[c] = handler
                        if c == FF31:
                            ids.append(g.store.recording_id)
                            handler(None, bytearray(b'e' * 20))
                            await a._drain_callbacks()
                    async def write_gatt_char(self, c, value, **kwargs):
                        if value == b'\x05':
                            self.handlers[FF32](None, bytearray(b'\x00\x00'))
                            await a._drain_callbacks()
                            if fail: raise OSError('synthetic start failure')
                    async def disconnect(self):
                        self.is_connected = False
                        self.callback(self)
                device = SimpleNamespace(name='Flowtime', metadata={'uuids': ['profile']}, rssi=-60)
                fake = SimpleNamespace(BleakClient=Client, BleakScanner=SimpleNamespace(discover=AsyncMock(return_value=[device])))
                with patch.dict(sys.modules, {'bleak': fake}):
                    task = asyncio.create_task(a.run())
                    try:
                        await asyncio.wait_for((ended if fail else connected).wait(), 2)
                        self.assertIsNotNone(ids[0])
                        for stream, value in [('eeg', b'e' * 20), ('wear', b'\x00\x00')]:
                            rows = [json.loads(x) for x in (root/'sessions'/ids[0]/'raw'/f'{stream}.jsonl').read_text().splitlines()]
                            self.assertEqual([base64.b64decode(x['bytesBase64']) for x in rows], [value])
                        if fail:
                            self.assertIsNone(g.store.recording_id)
                        else:
                            self.assertEqual(g.store.recording_id, ids[0])
                    finally:
                        await a.stop()
                        await asyncio.wait_for(task, 2)
                        await g.stop()

    async def test_timer_and_packet_windows_cannot_evaluate_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            g = Gateway(config(Path(directory)))
            await g.update_status('connectionState', 'connected')
            entered, release = asyncio.Event(), asyncio.Event()
            active = 0
            maximum = 0
            completed = []
            class Algorithm:
                available = True
                error = None
                async def evaluate(self, w):
                    nonlocal active, maximum
                    active += 1; maximum = max(maximum, active)
                    entered.set()
                    await release.wait()
                    active -= 1
                    completed.append(w.end_ms)
                    return {'attention': w.end_ms}, []
                async def stop(self): pass
            g.algorithm = Algorithm()
            g.assembler.add('ff31', b'e'*20, 1000)
            g._window_flush_deadline_ms = 1200
            timer = asyncio.create_task(g._flush_window_at(1200))
            g._window_flush_task = timer
            await asyncio.wait_for(entered.wait(), 1)
            with patch('neurobridge.business.gateway.now_ms', return_value=1801):
                packet = asyncio.create_task(g.receive_packet('ff31', b'f'*20))
                await asyncio.sleep(0)
                self.assertFalse(packet.done())
                release.set()
                await asyncio.gather(timer, packet)
            self.assertEqual(maximum, 1)
            self.assertIn(1200, completed)
            await g.stop()


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'bridge.py'
        self.runner = None

    async def asyncTearDown(self):
        if self.runner: await self.runner.stop()
        self.directory.cleanup()

    async def bridge(self, body, timeout=1):
        self.path.write_text(body)
        self.runner = AlgorithmRunner(AlgorithmConfig(True, (sys.executable, str(self.path)), int(timeout * 1000), .1))
        await self.runner.start()
        return self.runner

    async def test_concurrent_requests_keep_correct_responses(self):
        r = await self.bridge('import sys,json,time\nfor line in sys.stdin:\n q=json.loads(line); time.sleep(.03); print(json.dumps({"algorithm":{"attention":q["timestampMs"]}}),flush=True)\n')
        results = await asyncio.gather(*(r.evaluate(window(end)) for end in (1200,1800,2400)))
        self.assertEqual([x[0]['attention'] for x in results], [1200,1800,2400])

    async def test_timeout_retires_stream_and_next_session_is_clean(self):
        r = await self.bridge('import sys,json,time\nfor line in sys.stdin:\n q=json.loads(line); time.sleep(.3); print(json.dumps({"algorithm":{"attention":q["timestampMs"]}}),flush=True)\n', .1)
        result = await r.evaluate(window(1200))
        self.assertEqual(result, (None, ['ALGORITHM_ERROR']))
        self.assertIsNone(r.process)
        self.assertTrue(r.error)
        self.assertEqual(await r.evaluate(window(1800)), (None, ['ALGORITHM_NOT_READY']))
        self.path.write_text('import sys,json\nfor line in sys.stdin:\n q=json.loads(line); print(json.dumps({"algorithm":{"attention":q["timestampMs"]}}),flush=True)\n')
        # Recovery checks response ownership, not interpreter cold-start latency.
        r.config = replace(r.config, request_timeout_ms=1000)
        await r.initialize()
        self.assertEqual((await r.evaluate(window(2400)))[0]['attention'], 2400)

    async def test_cancelled_exchange_cannot_leak_response_into_next_call(self):
        r = await self.bridge('import sys,time\nfor line in sys.stdin: time.sleep(10)\n')
        task = asyncio.create_task(r.evaluate(window(1200)))
        await asyncio.sleep(.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNone(r.process)
        self.assertEqual(await r.evaluate(window(1800)), (None, ['ALGORITHM_NOT_READY']))

    async def test_hung_bridge_does_not_block_disconnect_and_recording_close(self):
        r = await self.bridge('import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint("READY",flush=True)\nwhile True: time.sleep(1)\n')
        await asyncio.wait_for(r.process.stdout.readline(), 1)
        process = r.process
        g = Gateway(config(Path(self.directory.name)/'recordings'))
        g.algorithm = r
        await g.update_status('connectionState','connected')
        with self.assertLogs('neurobridge.algorithm.runner', level='WARNING') as logs:
            await asyncio.wait_for(g.update_status('connectionState','disconnected'), 1)
        self.assertIn('killing pid=', '\n'.join(logs.output))
        self.assertIsNotNone(process.returncode)
        self.assertIsNone(g.store.recording_id)
        await g.stop()


class MonitorTests(unittest.TestCase):
    def test_hci_whitelist_excludes_payload_keys_addresses_and_unrelated_packets(self):
        parser = DisconnectFilter()
        lines = ['> ACL Data RX: PRIVATE_RAW', '    Handle: 1', '    Value: PRIVATE_RAW',
                 '> HCI Event: Disconnect Complete (0x05) plen 4 [hci0] 2026-11-03 11:13:04',
                 '    Status: Success (0x00)', '    Handle: 64', '    Reason: Connection Timeout (0x08)',
                 '    Key: PRIVATE_KEY', '> ACL Data RX:', '    Handle: 22',
                 '< HCI Command: Disconnect (0x01|0x0006)', '    Reason: Remote User Terminated Connection (0x13)']
        output = '\n'.join(x for l in lines if (x := parser.feed(l)))
        self.assertIn('Connection Timeout', output)
        self.assertIn('HCI Command: Disconnect', output)
        self.assertNotIn('PRIVATE', output)
        self.assertNotIn('Handle: 22', output)

    def test_algorithm_timeouts_validate_and_keep_old_configuration_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.toml'
            path.write_text('[data_source]\ntype="bluetooth"\n' + '')
            self.assertEqual(load(path).algorithm.stop_timeout_seconds, 1)
            for key in ('evaluation_timeout_seconds', 'stop_timeout_seconds'):
                for value in ('0', '-1', 'nan', 'inf'):
                    path.write_text('[data_source]\ntype="bluetooth"\n' + f'[algorithm]\n{key} = {value}\n')
                    with self.assertRaises(ValueError): load(path)


class MonitorProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pipe_filters_trace_and_exits_on_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root/'btmon'
            executable.write_text(f'#!{sys.executable}\nimport time\nprint("> ACL Data RX: PRIVATE_RAW",flush=True)\nprint("> HCI Event: Disconnect Complete (0x05) plen 4",flush=True)\nprint("    Reason: Connection Timeout (0x08)",flush=True)\nprint("    Key: PRIVATE_KEY",flush=True)\ntime.sleep(30)\n')
            executable.chmod(0o700)
            output = root/'ble-link.log'
            with patch.dict(os.environ, {'PATH': directory + os.pathsep + os.environ['PATH']}):
                await asyncio.wait_for(monitor(output, 'hci0', 1), 3)
            contents = output.read_text()
            self.assertIn('Connection Timeout', contents)
            self.assertIn('monitor stopped', contents)
            self.assertNotIn('PRIVATE', contents)
