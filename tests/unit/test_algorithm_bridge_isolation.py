from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from neurobridge.algorithm.runner import AlgorithmRunner
from neurobridge.config import AlgorithmConfig


class BridgeProcess:
    """Controllable pipe without an SDK, device, or external process."""

    def __init__(self, *, blocked_drain: bool = False) -> None:
        self.pid = 42
        self.returncode = None
        self.stdin = self
        self.stdout = asyncio.StreamReader()
        self.writes: list[bytes] = []
        self.written = asyncio.Event()
        self.blocked_drain = blocked_drain
        self.terminated = False

    def write(self, value: bytes) -> None:
        self.writes.append(value)
        self.written.set()

    async def drain(self) -> None:
        if self.blocked_drain:
            await asyncio.Event().wait()

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    async def wait(self) -> int:
        return self.returncode


class BridgeIsolationTests(unittest.IsolatedAsyncioTestCase):
    def runner(self, process: BridgeProcess) -> AlgorithmRunner:
        runner = AlgorithmRunner(AlgorithmConfig(True, ("bridge",), request_timeout_ms=20))
        runner.process = process  # type: ignore[assignment]
        return runner

    async def evaluate(self, runner: AlgorithmRunner):
        return await runner.evaluate_raw(b"synthetic", b"", start_ms=0, end_ms=600)

    async def test_timeout_discards_late_response_and_recovers_only_with_new_session(self) -> None:
        old = BridgeProcess()
        runner = self.runner(old)
        self.assertEqual(await self.evaluate(runner), (None, ["ALGORITHM_ERROR"]))
        old.stdout.feed_data(b'{"algorithm":{"attention":1}}\n')
        self.assertTrue(old.terminated)
        self.assertFalse(runner.available)
        self.assertEqual(await self.evaluate(runner), (None, ["ALGORITHM_NOT_READY"]))
        self.assertEqual(len(old.writes), 1)
        fresh = BridgeProcess()
        fresh.stdout.feed_data(b'{"algorithm":{"attention":2}}\n')
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fresh)):
            await runner.initialize()
        self.assertEqual(await self.evaluate(runner), ({"attention": 2}, []))
        self.assertIsNone(runner.error)
        await runner.stop()

    async def test_pipe_backpressure_is_also_bounded(self) -> None:
        process = BridgeProcess(blocked_drain=True)
        runner = self.runner(process)
        result = await asyncio.wait_for(self.evaluate(runner), timeout=1)
        self.assertEqual(result, (None, ["ALGORITHM_ERROR"]))
        self.assertTrue(process.terminated)

    async def test_cancelled_exchange_cannot_supply_next_request(self) -> None:
        process = BridgeProcess()
        runner = self.runner(process)
        task = asyncio.create_task(self.evaluate(runner))
        await process.written.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        process.stdout.feed_data(b'{"algorithm":{"attention":1}}\n')
        self.assertTrue(process.terminated)
        self.assertEqual(await self.evaluate(runner), (None, ["ALGORITHM_NOT_READY"]))

    async def test_non_object_and_missing_algorithm_are_invalid_without_raising(self) -> None:
        for response in (b"[]\n", b"null\n", b"{}\n", b'{"algorithm":[]}\n'):
            with self.subTest(response=response):
                process = BridgeProcess()
                process.stdout.feed_data(response)
                runner = self.runner(process)
                self.assertEqual(await self.evaluate(runner), (None, ["ALGORITHM_OUTPUT_INVALID"]))
                self.assertTrue(process.terminated)

    async def test_malformed_json_discards_entire_pipe(self) -> None:
        process = BridgeProcess()
        process.stdout.feed_data(b'not-json\n{"algorithm":{"attention":1}}\n')
        runner = self.runner(process)
        self.assertEqual(await self.evaluate(runner), (None, ["ALGORITHM_ERROR"]))
        self.assertEqual(await self.evaluate(runner), (None, ["ALGORITHM_NOT_READY"]))
        self.assertTrue(process.terminated)

    async def test_old_cancellation_does_not_close_replacement_process(self) -> None:
        old = BridgeProcess()
        runner = self.runner(old)
        task = asyncio.create_task(self.evaluate(runner))
        await old.written.wait()
        fresh = BridgeProcess()
        fresh.stdout.feed_data(b'{"algorithm":{"attention":2}}\n')
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fresh)):
            await runner.initialize()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(old.terminated)
        self.assertFalse(fresh.terminated)
        self.assertIsNone(runner.error)
        self.assertEqual(await self.evaluate(runner), ({"attention": 2}, []))
        await runner.stop()
