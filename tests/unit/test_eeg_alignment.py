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
        "hr": {"value": 69 if is_headset else 72, "hrv": 44 if is_headset else 60},
        "pressure": 91.41 if is_headset else 32,
        "coherence": 9 if is_headset else 30,
        "arousal": 11 if is_headset else 70,
    }
    result = AlgorithmResult(batch.batch_id, "test", start, start + 600, metrics, valid,
                             () if valid else ("ALGORITHM_ERROR",))
    return WindowResult(batch, result, "live", start + 600, True)


class EegAlignmentTests(unittest.IsolatedAsyncioTestCase):
    def cache(self, wait_ms=0, clock_ms=lambda: 3000):
        cache = EegAlignmentCache(percent=5, wait_ms=wait_ms, clock_ms=clock_ms)
        cache.set_session("headset_rev181", "session")
        cache.set_session("headband_ble", "session")
        return cache

    async def warm(self, cache):
        # Hardware clocks are independent. Reference sequence wraps while the headset sequence does not.
        for start in range(0, 2400, 600):
            for protocol, sequence in (("headset_rev181", 100), ("headband_ble", 65535)):
                packets = [(sequence + t // 12, t, 42) for t in range(start, start + 600, 12)]
                await cache.align_result(window(protocol, [-2, 0, 2], [], start=start, packets=packets))

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
        self.assertEqual(metrics["eeg"]["bandPower"], {"alpha": 38, "gamma": None})
        self.assertEqual(metrics["attention"], 38)
        self.assertEqual(metrics["relaxation"], 42)
        self.assertEqual(metrics["pleasure"], 0)
        self.assertEqual(metrics["flow"], {"meditation": 28.5, "meditationTips": 1})
        self.assertEqual(metrics["sleep"], {"state": 3})
        self.assertEqual(metrics["hr"], {"value": 69, "hrv": 44})
        self.assertEqual(metrics["pressure"], 91.41)
        self.assertEqual(metrics["coherence"], 9)
        self.assertEqual(metrics["arousal"], 11)
        self.assertEqual(original.algorithm_result.metrics["eeg"]["wave"]["left"], [-100, 0, 100])
        self.assertIs(actual.batch, original.batch)

    async def test_preserves_eeg_scores_within_tolerance_and_zero_reference(self):
        cache = self.cache()
        reference = window("headband_ble", [-123, -120], [])
        reference.algorithm_result.metrics["flow"]["meditation"] = 0
        await cache.align_result(reference)
        headset = window("headset_rev181", [-1000, -800], [])
        headset.algorithm_result.metrics["attention"] = 39
        result = await cache.align_result(headset)
        self.assertEqual(result.algorithm_result.metrics["attention"], 39)
        self.assertEqual(result.algorithm_result.metrics["flow"]["meditation"], 10)

    async def test_zero_flow_uses_the_reference_that_paired_with_valid_headset_eeg(self):
        for reference_start in (2400, 3000):
            with self.subTest(reference_start=reference_start):
                cache = self.cache()
                await self.warm(cache)
                # Pairing counts even when the wave is already inside tolerance.
                wave = [-123, -120, 10]
                headset = window("headset_rev181", wave, [], packets=[(300, 2400, 42)])
                headset.algorithm_result.metrics["flow"]["meditation"] = 0
                reference = window("headband_ble", wave, [], start=reference_start,
                                   packets=[(65735, reference_start, 42)])
                self.assertIs(await cache.align_result(reference), reference)
                with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
                    result = await cache.align_result(headset)
                self.assertIn("reason=flow_fallback", logs.output[0])
                self.assertIn(f"flowFallbackReferenceBatchId={reference.batch.batch_id}", logs.output[0])
                self.assertEqual(result.algorithm_result.metrics["flow"], {"meditation": 28.5, "meditationTips": 1})
                self.assertEqual(headset.algorithm_result.metrics["flow"]["meditation"], 0)
                self.assertEqual(reference.algorithm_result.metrics["flow"]["meditation"], 30)

    async def test_nonzero_flow_is_aligned_when_headset_eeg_is_usable(self):
        cache = self.cache()
        await self.warm(cache)
        wave = [-123, -120, 10]
        headset = window("headset_rev181", wave, [], packets=[(300, 2400, 42)])
        headset.algorithm_result.metrics["flow"]["meditation"] = 10
        reference = window("headband_ble", wave, [], packets=[(65735, 2400, 42)])
        await cache.align_result(reference)
        result = await cache.align_result(headset)
        self.assertEqual(result.algorithm_result.metrics["flow"]["meditation"], 28.5)
        self.assertEqual(headset.algorithm_result.metrics["flow"]["meditation"], 10)

    async def test_zero_flow_stays_zero_without_usable_eeg_pairing_and_reference_score(self):
        for quality, ear_packets, band_packets, band_flow in (
            (0, [(300, 2400, 42)], [(65735, 2400, 42)], 30),
            (1, [(300, 2400, 42)], [(65735, 2400, 255)], 30),
            (1, [(300, 2400, 255)], [(65735, 2400, 42)], 30),
            (1, [], [(65735, 2400, 42)], 30),
            (1, [(300, 2400, 42)], [(65735, 2400, 42)], 0),
            (1, [(300, 2400, 42)], [(65735, 2400, 42)], float("nan")),
        ):
            with self.subTest(quality=quality, ear_packets=ear_packets, band_packets=band_packets, band_flow=band_flow):
                cache = self.cache()
                await self.warm(cache)
                headset = window("headset_rev181", [-100, 0, 100], [], packets=ear_packets)
                headset.algorithm_result.metrics["eeg"]["quality"] = quality
                headset.algorithm_result.metrics["flow"]["meditation"] = 0
                reference = window("headband_ble", [-600, 0, 600], [], packets=band_packets)
                reference.algorithm_result.metrics["flow"]["meditation"] = band_flow
                await cache.align_result(reference)
                result = await cache.align_result(headset)
                self.assertEqual(result.algorithm_result.metrics["flow"]["meditation"], 0)

    async def test_same_window_headband_calibrates_without_waiting_out_the_deadline(self):
        cache = self.cache(clock_ms=lambda: 2400)
        await self.warm(cache)
        headset = window("headset_rev181", [-1000, -800, 10], [], packets=[(300, 2400, 42)])
        pending = asyncio.create_task(cache.align_result(headset))
        await asyncio.sleep(0.05)
        self.assertFalse(pending.done())
        reference = window("headband_ble", [-123, -120, 10], [], packets=[(65735, 2400, 42)])
        self.assertIs(await cache.align_result(reference), reference)
        result = await asyncio.wait_for(pending, 0.2)
        self.assertEqual(result.algorithm_result.metrics["eeg"]["wave"]["left"], [-129.15, -126.0, 10])
        self.assertTrue(result.algorithm_result.valid)

    async def test_next_receive_window_does_not_pair_with_this_headset_window(self):
        cache = self.cache(clock_ms=lambda: 2400)
        await self.warm(cache)
        original = window("headset_rev181", [-1000, -800, 10], [], packets=[(300, 2400, 42)])
        pending = asyncio.create_task(cache.align_result(original))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        await cache.align_result(window("headband_ble", [-123, -120, 10], [], start=3000,
                                        packets=[(sequence, 3000 + index * 12, 42)
                                                 for index, sequence in enumerate(range(65735, 65785))]))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    async def test_previous_window_does_not_release_the_current_headset_window(self):
        cache = self.cache(clock_ms=lambda: 2400)
        await self.warm(cache)
        headset = window("headset_rev181", [-1000, -800, 102], [], packets=[(300, 2400, 42)])
        pending = asyncio.create_task(cache.align_result(headset))
        await asyncio.sleep(0.05)
        self.assertFalse(pending.done())
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

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

    async def test_unmatched_headset_window_is_dropped_after_one_window(self):
        cache = self.cache(clock_ms=lambda: 2400)
        await self.warm(cache)
        headset = window("headset_rev181", [-1000, -800, 102], [], packets=[(300, 2400, 42)])
        stop = asyncio.Event()

        async def keep_headband_on_older_windows():
            # These are real headband windows, but they are not this headset window.
            sequence = 0
            while not stop.is_set():
                await asyncio.sleep(0.05)
                await cache.align_result(window("headband_ble", [-123, -120, 10], [], start=0,
                                                packets=[(sequence, 0, 42)]))
                sequence += 1

        started = asyncio.get_running_loop().time()
        stream = asyncio.create_task(keep_headband_on_older_windows())
        try:
            result = await asyncio.wait_for(cache.align_result(headset), 1)
        finally:
            stop.set()
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        elapsed = asyncio.get_running_loop().time() - started
        self.assertGreaterEqual(elapsed, 0.6)
        self.assertLess(elapsed, 0.9)
        self.assertIs(result.batch, headset.batch)
        self.assertFalse(result.algorithm_result.valid)
        self.assertIn("REFERENCE_WAIT_EXPIRED", result.algorithm_result.invalid_reasons)
        self.assertEqual(result.algorithm_result.metrics["eeg"]["wave"]["left"], [])
        self.assertEqual(headset.algorithm_result.metrics["eeg"]["wave"]["left"], [-1000, -800, 102])

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
            self.assertIs(await asyncio.wait_for(cache.align_result(headset), 0.1), headset)
        self.assertIn("reason=headset_raw_eeg_all_ff", logs.output[0])
        self.assertIn("reason=headband_raw_eeg_all_ff", logs.output[1])
