import unittest
from dataclasses import replace

from neurobridge.application.eeg_alignment import EegAlignmentCache
from neurobridge.domain.algorithm import AlgorithmResult
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def window(protocol, left, right, *, start=600, valid=True):
    batch = ParsedSignalBatch(protocol, protocol, "session", "recording", start, start + 600, (), ())
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


class EegAlignmentTests(unittest.TestCase):
    def test_calibrates_waveforms_and_eeg_scores_with_completed_reference(self):
        cache = EegAlignmentCache(percent=5)
        original = window("headset_rev181", [-100, 0, 100], [-200, 0, 200])
        reference = window("headband_ble", [-600, 0, 600], [-100, 100])
        self.assertIs(cache.align_result(original), original)
        self.assertIs(cache.align_result(reference), reference)
        actual = cache.align_result(original)
        metrics = actual.algorithm_result.metrics
        self.assertEqual(metrics["eeg"]["wave"], {"left": [-570, 0, 570], "right": [-105, 95, 200]})
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

    def test_uses_preceding_window_if_current_reference_is_unavailable(self):
        cache = EegAlignmentCache(percent=5)
        cache.align_result(window("headband_ble", [-600, 0, 600], [-600, 0, 600], start=0))
        original = window("headset_rev181", [-100, 0, 100], [-200, 0, 200])
        self.assertIsNot(cache.align_result(original), original)
        later = window("headset_rev181", [-100, 0, 100], [-200, 0, 200], start=1200)
        self.assertIs(cache.align_result(later), later)
        cache.align_result(window("headband_ble", [], [], valid=False))
        self.assertIs(cache.align_result(original), original)

    def test_leaves_flat_channels_and_invalid_output_untouched(self):
        cache = EegAlignmentCache(percent=5)
        cache.align_result(window("headband_ble", [-600, 0, 600], [-600, 0, 600]))
        original = window("headset_rev181", [-100, 0, 100], [0, 0, 0])
        self.assertEqual(cache.align_result(original).algorithm_result.metrics["eeg"]["wave"]["right"], [0, 0, 0])
        for left, right, valid in (
            ([], [], True), ([0] * 3, [0] * 3, True), ([255] * 3, [-1] * 3, True),
            ([-100, 0, 100], [-100, 0, 100], False),
        ):
            unavailable = window("headset_rev181", left, right, valid=valid)
            self.assertIs(cache.align_result(unavailable), unavailable)

    def test_limits_points_to_five_percent_of_headband_samples(self):
        cache = EegAlignmentCache(percent=5)
        reference = window("headband_ble", [-123, -120, 100, 0, 1999], [])
        original = window("headset_rev181", [-1000, -800, 102, 20, 1000, 500], [])
        cache.align_result(reference)
        self.assertEqual(cache.align_result(original).algorithm_result.metrics["eeg"]["wave"]["left"],
                         [-129.15, -126, 102, 0, 1899.05, 500])

    def test_skips_calibration_for_an_all_ff_raw_eeg_packet_on_either_device(self):
        def signal(kind, payload):
            return ParsedSignal(kind, payload, "bytes", None, {}, (), 600)

        def with_signals(result, *signals):
            return replace(result, batch=replace(result.batch, signals=signals))

        cache = EegAlignmentCache(percent=5)
        reference = window("headband_ble", [-123, -120], [])
        headset = window("headset_rev181", [-1000, -800], [])
        valid_reference = with_signals(reference, signal("eeg", b"\xff\xff" + b"\xff" * 17 + b"\x01"))
        cache.align_result(valid_reference)
        headset_with_ff_hr = with_signals(headset, signal("hr", b"\xff"))
        self.assertIsNot(cache.align_result(headset_with_ff_hr), headset_with_ff_hr)
        unworn_headset = with_signals(
            headset, signal("eeg", b"\x00" * 18), signal("eeg", b"\xff" * 18), signal("hr", b"\x45"),
        )
        with self.assertLogs("neurobridge.application.eeg_alignment", level="INFO") as logs:
            self.assertIs(cache.align_result(unworn_headset), unworn_headset)
            cache.align_result(with_signals(reference, signal("eeg", b"\x00\x01" + b"\xff" * 18)))
            self.assertIs(cache.align_result(headset), headset)
        self.assertIn("reason=headset_raw_eeg_all_ff", logs.output[0])
        self.assertIn("reason=headband_raw_eeg_all_ff", logs.output[1])
