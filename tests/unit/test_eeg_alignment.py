import asyncio
import base64
import unittest

from neurobridge.application.eeg_alignment import EegAlignmentCache
from neurobridge.domain.algorithm import AlgorithmInput
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def packet(samples, sequence=7):
    return sequence.to_bytes(2, "big") + b"".join(sample.to_bytes(3, "big") for sample in samples)


def window(protocol, session, raw, start=600):
    signals = (ParsedSignal("eeg", raw, "bytes", None, {}, (), start),) if raw else ()
    return ParsedSignalBatch(protocol, protocol, session, "recording", start, start + 600, signals, ())


def headset_input(raw):
    return AlgorithmInput("headset_rev181", {
        "eegRawBase64": base64.b64encode(raw).decode("ascii"),
        "hrRawBase64": "SQ==",
        "eegPacketCount": 1,
    }, "1", "headset_rev181")


class EegAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_matching_headband_window_and_changes_only_headset_eeg_samples(self):
        cache = EegAlignmentCache(percent=5, wait_ms=100, max_windows=2, headband_session=lambda: "ble")
        raw = packet([900, 1800, 1000, 2000, 1100, 2200])
        batch = window("headset_rev181", "serial", raw[2:])
        original = headset_input(raw)
        pending = asyncio.create_task(cache.prepare_headset(batch, original))
        cache.observe_headband(window("headband_ble", "ble", packet([400, 400, 1000, 1000, 1600, 1600]), start=0))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        cache.observe_headband(window("headband_ble", "ble", packet([400, 400, 1000, 1000, 1600, 1600])))
        actual = await pending
        self.assertEqual(base64.b64decode(actual.payload["eegRawBase64"]), packet([895, 1790, 1000, 2000, 1105, 2210]))
        self.assertEqual(actual.payload["hrRawBase64"], original.payload["hrRawBase64"])
        self.assertEqual(actual.payload["eegPacketCount"], 1)
        self.assertEqual(base64.b64decode(original.payload["eegRawBase64"]), raw)
        self.assertEqual(batch.signals[0].samples, raw[2:])

    async def test_falls_back_without_valid_data_from_current_sessions(self):
        session = "ble-1"
        cache = EegAlignmentCache(percent=5, wait_ms=0, max_windows=1, headband_session=lambda: session)
        raw = packet([900, 1800, 1000, 2000, 1100, 2200])
        original = headset_input(raw)
        batch = window("headset_rev181", "serial", raw[2:])
        with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
            self.assertIs(await cache.prepare_headset(batch, original), original)
        self.assertIn("reason=reference_timeout", logs.output[0])
        reference = packet([400, 400, 1000, 1000, 1600, 1600])
        cache.observe_headband(window("headband_ble", session, reference))
        for unusable in (b"", packet([0xFFFFFF, 1800, 0xFFFFFF, 2000, 0xFFFFFF, 2200])):
            input_value = headset_input(unusable)
            self.assertIs(await cache.prepare_headset(window("headset_rev181", "serial", unusable), input_value), input_value)
        cache.observe_headband(window("headband_ble", session, reference, start=1200))
        self.assertIs(await cache.prepare_headset(batch, original), original)
        cache.observe_headband(window("headband_ble", session, reference))
        session = "ble-2"
        self.assertIs(await cache.prepare_headset(batch, original), original)
        cache.observe_headband(window("headband_ble", session, packet([0xFFFFFF] * 6)))
        with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
            self.assertIs(await cache.prepare_headset(batch, original), original)
        self.assertIn("reason=headband_near_adc_rail", logs.output[0])
        session = None
        self.assertIs(await cache.prepare_headset(batch, original), original)
