import asyncio
import time
import unittest
from unittest.mock import patch

from neurobridge.application.eeg_alignment import EegAlignmentCache
from neurobridge.domain.raw import DeviceFrame, RawChunk
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def packet(samples, sequence=7):
    return sequence.to_bytes(2, "big") + b"".join(sample.to_bytes(3, "big") for sample in samples)


def window(protocol, session, raw, start=600):
    signals = (ParsedSignal("eeg", raw, "bytes", None, {}, (), start),) if raw else ()
    return ParsedSignalBatch(protocol, protocol, session, "recording", start, start + 600, signals, ())


def raw_eeg(cache, source, session, *, timestamp_ms=0, received_ns=None):
    cache.observe_raw_eeg(RawChunk(source, "ff31" if source == "bluetooth" else "serial", b"raw", timestamp_ms,
                                  time.monotonic_ns() if received_ns is None else received_ns, session, "raw-1"))


def headset_frames(raw):
    if not raw:
        return ()
    frame = b"\xaa\xaa\xaa\x1c" + raw + b"\x49\xbb\xbb\xbb"
    return (DeviceFrame("headset_rev181", frame, "frame-1", 7, 600, (), "serial"),)


class EegAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_matching_headband_window_and_changes_only_headset_eeg_samples(self):
        cache = EegAlignmentCache(percent=5, wait_ms=100, raw_timeout_ms=300, max_windows=2,
                                  headset_session=lambda: "serial", headband_session=lambda: "ble")
        raw_eeg(cache, "serial", "serial")
        raw_eeg(cache, "bluetooth", "ble")
        raw = packet([900, 1800, 1000, 2000, 1100, 2200])
        batch = window("headset_rev181", "serial", raw[2:])
        original = headset_frames(raw)
        pending = asyncio.create_task(cache.prepare_headset(batch, original))
        cache.observe_headband(window("headband_ble", "ble", packet([400, 400, 1000, 1000, 1600, 1600]), start=0))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        cache.observe_headband(window("headband_ble", "ble", packet([400, 400, 1000, 1000, 1600, 1600])))
        actual = await pending
        self.assertEqual(actual, headset_frames(packet([895, 1790, 1000, 2000, 1105, 2210])))
        self.assertEqual(original[0].raw_bytes[4:24], raw)
        self.assertEqual(batch.signals[0].samples, raw[2:])

    async def test_falls_back_without_valid_data_from_current_sessions(self):
        session = "ble-1"
        cache = EegAlignmentCache(percent=5, wait_ms=0, raw_timeout_ms=300, max_windows=1,
                                  headset_session=lambda: "serial", headband_session=lambda: session)
        raw_eeg(cache, "serial", "serial")
        raw_eeg(cache, "bluetooth", session)
        raw = packet([900, 1800, 1000, 2000, 1100, 2200])
        original = headset_frames(raw)
        batch = window("headset_rev181", "serial", raw[2:])
        with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
            self.assertIs(await cache.prepare_headset(batch, original), original)
        self.assertIn("reason=reference_timeout", logs.output[0])
        reference = packet([400, 400, 1000, 1000, 1600, 1600])
        cache.observe_headband(window("headband_ble", session, reference))
        for unusable in (b"", packet([0xFFFFFF, 1800, 0xFFFFFF, 2000, 0xFFFFFF, 2200])):
            input_value = headset_frames(unusable)
            self.assertIs(await cache.prepare_headset(window("headset_rev181", "serial", unusable), input_value), input_value)
        cache.observe_headband(window("headband_ble", session, reference, start=1200))
        self.assertIs(await cache.prepare_headset(batch, original), original)
        cache.observe_headband(window("headband_ble", session, reference))
        session = "ble-2"
        self.assertIs(await cache.prepare_headset(batch, original), original)
        raw_eeg(cache, "bluetooth", session)
        cache.observe_headband(window("headband_ble", session, packet([0xFFFFFF] * 6)))
        with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
            self.assertIs(await cache.prepare_headset(batch, original), original)
        self.assertIn("reason=headband_near_adc_rail", logs.output[0])
        session = None
        self.assertIs(await cache.prepare_headset(batch, original), original)

    async def test_raw_silence_disables_calibration_and_recovery_requires_a_fresh_reference(self):
        cache = EegAlignmentCache(percent=5, wait_ms=0, raw_timeout_ms=300, max_windows=2,
                                  headset_session=lambda: "serial", headband_session=lambda: "ble")
        raw = packet([900, 1800, 1000, 2000, 1100, 2200])
        reference = packet([400, 400, 1000, 1000, 1600, 1600])
        batch = window("headset_rev181", "serial", raw[2:])
        original = headset_frames(raw)
        started = 1_000_000_000
        with patch("neurobridge.application.eeg_alignment.time.monotonic_ns", return_value=started) as clock:
            raw_eeg(cache, "serial", "serial")
            raw_eeg(cache, "bluetooth", "ble")
            cache.observe_headband(window("headband_ble", "ble", reference))
            self.assertIsNot(await cache.prepare_headset(batch, original), original)

            clock.return_value = started + 299_000_000
            raw_eeg(cache, "serial", "serial")
            self.assertIsNot(await cache.prepare_headset(batch, original), original)
            clock.return_value = started + 300_000_000
            self.assertIs(await cache.prepare_headset(batch, original), original)

            raw_eeg(cache, "bluetooth", "ble", timestamp_ms=601)
            cache.observe_headband(window("headband_ble", "ble", reference))
            self.assertIs(await cache.prepare_headset(batch, original), original)
            new_reference = window("headband_ble", "ble", reference, start=1200)
            cache.observe_headband(new_reference)
            new_batch = window("headset_rev181", "serial", raw[2:], start=1200)
            self.assertIsNot(await cache.prepare_headset(new_batch, original), original)

            clock.return_value = started + 599_000_000
            self.assertIs(await cache.prepare_headset(new_batch, original), original)
