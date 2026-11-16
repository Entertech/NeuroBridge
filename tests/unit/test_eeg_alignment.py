import asyncio
import unittest
import weakref
from dataclasses import replace

from neurobridge.application.eeg_alignment import EegAlignmentCache
from neurobridge.domain.algorithm import AlgorithmResult
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def window(protocol, left, right, *, start=2400, valid=True, packets=()):
    is_headset = protocol == "headset_rev181"
    signals = []
    for sequence, received_at, byte in packets:
        number = sequence % 65536
        eeg = bytes([byte]) * 18
        payload = eeg if is_headset else number.to_bytes(2, "big") + eeg
        signals.append(ParsedSignal("eeg", payload, "bytes", None, {"sequence": number}, (), received_at))
    batch = ParsedSignalBatch(f"{protocol}-{start}-{packets[:1]}", protocol, "session", "recording",
                              start, start + 600, tuple(signals), ())
    is_headset = protocol == "headset_rev181"
    metrics = {
        "eeg": {"wave": {"left": left, "right": right}, "quality": 1,
                "bandPower": {"alpha": 20 if is_headset else 40, "gamma": None}},
        "attention": 20 if is_headset else 40,
        "relaxation": 60 if is_headset else 40,
        "pleasure": 0 if is_headset else 40,
        "flow": {"meditation": 10 if is_headset else 30, "meditationTips": 1 if is_headset else 2},
        "sleep": {"state": 3 if is_headset else 2},
        "hr": {"value": 69 if is_headset else 72},
        "pressure": 91.41 if is_headset else 32,
    }
    result = AlgorithmResult(batch.batch_id, "test", start, start + 600, metrics, valid,
                             () if valid else ("ALGORITHM_ERROR",))
    return WindowResult(batch, result, "live", start + 600, True)


class EegAlignmentTests(unittest.IsolatedAsyncioTestCase):
    def cache(self, wait_ms=0):
        cache = EegAlignmentCache(percent=5, wait_ms=wait_ms)
        cache.set_session("headset_rev181", "session")
        cache.set_session("headband_ble", "session")
        return cache

    async def warm(self, cache):
        # Hardware clocks are independent. Reference sequence wraps while the headset sequence does not.
        for start in range(0, 2400, 600):
            for protocol, sequence in (("headset_rev181", 100), ("headband_ble", 65535)):
                packets = [(sequence + t // 12, t, 42) for t in range(start, start + 600, 12)]
                await cache.align_result(window(protocol, [], [], start=start, packets=packets))

    async def test_calibrates_waveforms_and_eeg_scores_with_completed_reference(self):
        cache = self.cache()
        await self.warm(cache)
        original = window("headset_rev181", [-100, 0, 100], [-200, 0, 200], packets=[(300, 2400, 42)])
        reference = window("headband_ble", [-600, 0, 600], [-100, 0, 100], packets=[(65735, 2400, 42)])
        self.assertIs(await cache.align_result(reference), reference)
        actual = await cache.align_result(original)
        metrics = actual.algorithm_result.metrics
        self.assertEqual(metrics["eeg"]["wave"], {"left": [-570, 0, 570], "right": [-105, 0, 105]})
        self.assertEqual(metrics["eeg"]["quality"], 1)
        self.assertEqual(metrics["eeg"]["bandPower"], {"alpha": 21, "gamma": None})
        self.assertEqual(metrics["attention"], 21)
        self.assertEqual(metrics["relaxation"], 59)
        self.assertEqual(metrics["pleasure"], 0)
        self.assertEqual(metrics["flow"], {"meditation": 11, "meditationTips": 1})
        self.assertEqual(metrics["sleep"], {"state": 3})
        self.assertEqual(metrics["hr"], {"value": 69})
        self.assertEqual(metrics["pressure"], 91.41)
        self.assertEqual(original.algorithm_result.metrics["eeg"]["wave"]["left"], [-100, 0, 100])
        self.assertIs(actual.batch, original.batch)

    async def test_matches_time_across_receive_windows_and_leaves_unmatched_points(self):
        cache = self.cache(wait_ms=60)
        await self.warm(cache)
        original = window("headset_rev181", [-1000, -800, 10, 11, 12, 13], [],
                          packets=[(300, 2400, 42), (301, 2412, 42)])
        # Simulate a BLE batch that arrived together late enough to end up in the next receive window.
        reference = window("headband_ble", [-123, -120, 10], [], start=3000, packets=[(65735, 3000, 42)])
        pending = asyncio.create_task(cache.align_result(original))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        await cache.align_result(reference)
        actual = await pending
        self.assertEqual(actual.algorithm_result.metrics["eeg"]["wave"]["left"], [-129.15, -126.0, 10, 11, 12, 13])
        # Adjacent waveform data does not supply a reference for window-level scores.
        self.assertEqual(actual.algorithm_result.metrics["attention"], 20)

    async def test_leaves_flat_channels_and_invalid_output_untouched(self):
        cache = self.cache()
        await cache.align_result(window("headband_ble", [-600, 0, 600], [-600, 0, 600]))
        original = window("headset_rev181", [-100, 0, 100], [0, 0, 0])
        self.assertEqual((await cache.align_result(original)).algorithm_result.metrics["eeg"]["wave"]["right"], [0, 0, 0])
        for left, right, valid in (
            ([], [], True), ([0] * 3, [0] * 3, True), ([255] * 3, [-1] * 3, True),
            ([-100, 0, 100], [-100, 0, 100], False),
        ):
            unavailable = window("headset_rev181", left, right, valid=valid)
            self.assertIs(await cache.align_result(unavailable), unavailable)

    async def test_disconnect_releases_wait_and_discards_old_reference(self):
        cache = self.cache(wait_ms=300)
        await self.warm(cache)
        headset = window("headset_rev181", [-1000, -800, 102], [], packets=[(300, 2400, 42)])
        pending = asyncio.create_task(cache.align_result(headset))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        cache.set_session("headband_ble", None)
        self.assertIs(await asyncio.wait_for(pending, 0.1), headset)

    async def test_full_caches_release_old_references_and_disconnect_releases_the_rest(self):
        class Wave(list):
            pass

        cache = EegAlignmentCache(percent=5, wait_ms=0, reference_batches=2, clock_packets=4)
        protocol = "headband_ble"
        cache.set_session(protocol, "session")
        old_wave = None
        for sequence in range(1000):
            wave = Wave([-2, 0, 2])
            retained_wave = weakref.ref(wave)
            if sequence == 0:
                old_wave = retained_wave
            await cache.align_result(window(protocol, wave, [], packets=[(sequence, sequence * 12, 42)]))
            del wave

        self.assertEqual(len(cache._references), 2)
        self.assertEqual(len(cache._clocks[protocol][1]._observations), 4)
        self.assertIsNone(old_wave())
        self.assertIsNotNone(retained_wave())
        cache.set_session(protocol, None)
        self.assertIsNone(retained_wave())
        self.assertNotIn(protocol, cache._clocks)
        self.assertFalse(cache._references)

    async def test_skips_calibration_for_an_all_ff_raw_eeg_packet_on_either_device(self):
        def signal(kind, payload):
            return ParsedSignal(kind, payload, "bytes", None, {}, (), 600)

        def with_signals(result, *signals):
            return replace(result, batch=replace(result.batch, signals=signals))

        cache = self.cache()
        reference = window("headband_ble", [-123, -120], [])
        headset = window("headset_rev181", [-1000, -800], [])
        valid_reference = with_signals(reference, signal("eeg", b"\xff\xff" + b"\xff" * 17 + b"\x01"))
        await cache.align_result(valid_reference)
        headset_with_ff_hr = with_signals(headset, signal("hr", b"\xff"))
        self.assertIsNot(await cache.align_result(headset_with_ff_hr), headset_with_ff_hr)
        unworn_headset = with_signals(
            headset, signal("eeg", b"\x00" * 18), signal("eeg", b"\xff" * 18), signal("hr", b"\x45"),
        )
        with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
            self.assertIs(await cache.align_result(unworn_headset), unworn_headset)
            await cache.align_result(with_signals(reference, signal("eeg", b"\x00\x01" + b"\xff" * 18)))
            self.assertIs(await cache.align_result(headset), headset)
        self.assertIn("reason=headset_raw_eeg_all_ff", logs.output[0])
        self.assertIn("reason=headband_raw_eeg_all_ff", logs.output[1])
