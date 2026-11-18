import unittest

from neurobridge.application.eeg_alignment import EegAlignmentCache
from neurobridge.application.gateway import _LegacyWindowAggregator
from neurobridge.domain.algorithm import AlgorithmResult
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def result(protocol: str, eeg_wave, metrics, *, end_ms: int, signals=()) -> WindowResult:
    batch = ParsedSignalBatch(
        f"batch-{protocol}-{end_ms}", protocol, "session", "recording",
        end_ms - 100, end_ms, tuple(signals), (),
    )
    return WindowResult(batch, AlgorithmResult(batch.batch_id, None, end_ms, end_ms, metrics, True, ()), "live", end_ms, True)


class LegacyAggregatorTests(unittest.TestCase):
    def test_merges_eeg_and_hr_metrics_into_one_600ms_batch(self):
        aggregator = _LegacyWindowAggregator(600)
        aggregator.add(result("headset_rev181", None, {"hr": {"value": 70}}, end_ms=1200))
        aggregator.add(result("headset_rev181", None, {"eeg": {"wave": {"left": [1]}}, "attention": 42}, end_ms=1250))
        ready = aggregator.pop_ready(1800)
        self.assertEqual(len(ready), 1)
        self.assertEqual(set(ready[0].algorithm_result.metrics), {"hr", "eeg", "attention"})
        self.assertEqual(ready[0].batch.window_end_ms, 1800)
        self.assertEqual(aggregator.pop_ready(2400), [])


class RelayAlignmentTests(unittest.TestCase):
    def cache(self):
        cache = EegAlignmentCache()
        cache.relay_mode = True
        cache.set_session("headset_rev181", "session")
        cache.set_session("headband_ble", "session")
        cache._worn["headset_rev181"] = True
        cache._worn["headband_ble"] = True
        cache._latest_eeg = {"eeg": {"wave": {"left": [10, 20, 30], "right": [40, 50, 60]}}, "attention": 80}
        return cache

    def test_relay_ready_and_variation_keeps_wave_length(self):
        cache = self.cache()
        self.assertTrue(cache.relay_ready())
        metrics = cache.varied_headband_metrics()
        self.assertEqual(len(metrics["eeg"]["wave"]["left"]), 3)
        for value, reference in zip(metrics["eeg"]["wave"]["left"], (10, 20, 30)):
            self.assertGreaterEqual(value, reference * 0.80)
            self.assertLessEqual(value, reference * 1.20)

    def test_headset_result_is_hr_only_while_relay_is_ready(self):
        cache = self.cache()
        import asyncio
        headset = result("headset_rev181", None, {"eeg": {"wave": {"left": [1]}}, "hr": {"value": 70}, "pressure": 40, "attention": 20}, end_ms=1200)
        aligned = asyncio.run(cache.align_result(headset))
        self.assertNotIn("eeg", aligned.algorithm_result.metrics)
        self.assertNotIn("attention", aligned.algorithm_result.metrics)
        self.assertEqual(aligned.algorithm_result.metrics["hr"], {"value": 70})


if __name__ == "__main__":
    unittest.main()
