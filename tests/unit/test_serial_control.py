from __future__ import annotations

import asyncio
import unittest

from neurobridge.adapters.sources.serial_control import SerialSessionControl


class SerialSessionControlTests(unittest.TestCase):
    def test_control_is_session_bound_and_each_command_is_sent_once(self) -> None:
        async def scenario() -> None:
            current = "conn-1"
            existing = False
            writes: list[bytes] = []

            async def write(value: bytes) -> None:
                writes.append(value)

            control = SerialSessionControl(lambda: current, lambda: existing, write)
            self.assertEqual((await control.start_stream("old")).outcome, "staleSession")
            self.assertEqual((await control.start_stream("conn-1")).outcome, "started")
            self.assertEqual((await control.start_stream("conn-1")).outcome, "alreadyStreaming")
            self.assertEqual((await control.stop_stream("conn-1")).outcome, "stopped")
            self.assertEqual((await control.stop_stream("conn-1")).outcome, "alreadyStopped")
            self.assertEqual(writes, [b"\xE1", b"\xE0"])

        asyncio.run(scenario())

    def test_existing_stream_never_sends_e1(self) -> None:
        async def scenario() -> None:
            writes: list[bytes] = []

            async def write(value: bytes) -> None:
                writes.append(value)

            control = SerialSessionControl(lambda: "conn-1", lambda: True, write)
            self.assertEqual((await control.start_stream("conn-1")).outcome, "alreadyStreaming")
            self.assertEqual((await control.stop_stream("conn-1")).outcome, "stopped")
            self.assertEqual(writes, [b"\xE0"])

        asyncio.run(scenario())
