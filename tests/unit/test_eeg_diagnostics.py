import unittest
from unittest.mock import patch

from neurobridge.adapters.headset_eeg_diagnostics import ADC_MAX, HeadsetEegDiagnostics
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def batch(samples, session="session"):
    eeg = b"".join(sample.to_bytes(3, "big") for sample in samples)
    signals = (
        ParsedSignal("eeg", eeg, "bytes", None, {}, (), 600),
        ParsedSignal("hr", b"\x45", "uint8", None, {}, (), 600),
    )
    return ParsedSignalBatch("batch-1", "headset_rev181", session, "recording", 600, 1200, signals, ())


class HeadsetEegDiagnosticsTests(unittest.TestCase):
    def test_reports_raw_patterns_and_channel_counts_without_dumping_samples(self):
        diagnostic = HeadsetEegDiagnostics()
        with self.assertLogs("neurobridge.adapters.headset_eeg_diagnostics", level="INFO") as logs:
            diagnostic.observe(batch([5000] * 6))
            diagnostic.observe(batch([0] * 6))
            diagnostic.observe(batch([ADC_MAX] * 6))
            diagnostic.observe(batch([0, ADC_MAX, 3, ADC_MAX - 2, 5000, 5000]))
            diagnostic.observe(batch([5000] * 6))

        self.assertEqual(len(logs.output), 4)
        self.assertIn("pattern=all_zero", logs.output[0])
        self.assertIn("batchId=batch-1", logs.output[0])
        self.assertIn("windowStartMs=600 windowEndMs=1200", logs.output[0])
        self.assertIn("eegPackets=1 hrPackets=1", logs.output[0])
        self.assertIn("pattern=all_ff", logs.output[1])
        self.assertIn("pattern=near_adc_rail", logs.output[2])
        self.assertIn("left={'samples': 3, 'zero': 1, 'ff': 0, 'nearLow': 1, 'nearHigh': 0}", logs.output[2])
        self.assertIn("right={'samples': 3, 'zero': 0, 'ff': 1, 'nearLow': 0, 'nearHigh': 1}", logs.output[2])
        self.assertNotIn("5000", logs.output[2])
        self.assertIn("pattern=within_adc_range previousPattern=near_adc_rail", logs.output[3])

    def test_limits_repeated_messages_and_resets_on_reconnect(self):
        diagnostic = HeadsetEegDiagnostics()
        zero = batch([0] * 6)
        with (
            patch("neurobridge.adapters.headset_eeg_diagnostics.time.monotonic", side_effect=[1, 2, 11, 12]),
            self.assertLogs("neurobridge.adapters.headset_eeg_diagnostics", level="INFO") as logs,
        ):
            diagnostic.observe(zero)
            diagnostic.observe(zero)
            diagnostic.observe(zero)
            diagnostic.observe(batch([0] * 6, session="reconnected"))
        self.assertEqual(len(logs.output), 3)
        self.assertIn("connectionSessionId=reconnected", logs.output[-1])
