import unittest
from dataclasses import replace

from neurobridge.application.eeg_alignment import EegAlignmentCache, display_eeg, raw_eeg_worn
from neurobridge.domain.algorithm import AlgorithmResult
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignal, ParsedSignalBatch


def eeg_bytes(pattern, *, protocol):
    if pattern == "signal":
        sample = b"\x00\x10\x20"
    elif pattern == "ff":
        sample = b"\xff\xff\xff"
    elif pattern == "zero":
        sample = b"\x00\x00\x00"
    else:
        raise ValueError(pattern)
    payload = sample * 6
    if protocol == "headband_ble":
        payload = b"\x00\x01" + payload
    return payload


def window(protocol, pattern, *, start=2400, valid=True, with_eeg=True):
    signals = []
    if with_eeg:
        signals.append(ParsedSignal("eeg", eeg_bytes(pattern, protocol=protocol), "bytes", None, {}, (), start))
    signals.append(ParsedSignal("hr", b"\x45", "uint8", None, {}, (), start))
    batch = ParsedSignalBatch(
        f"{protocol}-{pattern}-{start}", protocol, "session", "recording", start, start + 600, tuple(signals), (),
    )
    metrics = {
        "eeg": {"wave": {"left": [1, 2, 3] if protocol == "headset_rev181" else [8, 9, 10], "right": [4, 5, 6]},
                "quality": 1},
        "attention": 20 if protocol == "headset_rev181" else 80,
        "relaxation": 21 if protocol == "headset_rev181" else 81,
        "pleasure": 22 if protocol == "headset_rev181" else 82,
        "flow": {"meditation": 23 if protocol == "headset_rev181" else 83},
        "sleep": {"state": 1 if protocol == "headset_rev181" else 2},
        "hr": {"value": 69 if protocol == "headset_rev181" else 72, "hrv": 44 if protocol == "headset_rev181" else 60},
        "pressure": 91 if protocol == "headset_rev181" else 32,
        "coherence": 9 if protocol == "headset_rev181" else 30,
        "arousal": 11 if protocol == "headset_rev181" else 70,
    }
    result = AlgorithmResult(batch.batch_id, "test", start, start + 600, metrics, valid, () if valid else ("ALGORITHM_ERROR",))
    return WindowResult(batch, result, "live", start + 600, True)


class WearDetectionTests(unittest.TestCase):
    def test_all_ff_or_either_zero_channel_is_unworn_and_hr_does_not_count(self):
        self.assertIs(raw_eeg_worn(window("headset_rev181", "signal").batch), True)
        self.assertIs(raw_eeg_worn(window("headband_ble", "signal").batch), True)
        self.assertIs(raw_eeg_worn(window("headset_rev181", "ff").batch), False)
        left_zero = ParsedSignal("eeg", b"\x00\x00\x00" + b"\x00\x10\x20" + b"\x00" * 12, "bytes", None, {}, (), 1)
        self.assertIs(raw_eeg_worn(replace(window("headset_rev181", "signal").batch, signals=(left_zero,))), False)
        right_zero = ParsedSignal("eeg", b"\x00\x10\x20" + b"\x00\x00\x00" + b"\x00" * 12, "bytes", None, {}, (), 1)
        self.assertIs(raw_eeg_worn(replace(window("headset_rev181", "signal").batch, signals=(right_zero,))), False)
        self.assertIsNone(raw_eeg_worn(window("headset_rev181", "signal", with_eeg=False).batch))

    def test_display_rule_uses_both_devices_for_eeg_only(self):
        cases = (
            (True, False, None, "headset", True),
            (True, True, True, "headset", True),
            (True, True, True, "headband", True),
            (False, True, True, "headset", False),
            (False, True, True, "headband", True),
            (False, True, False, "headset", False),
            (False, True, False, "headband", False),
            (None, True, False, "headset", False),
            (True, True, False, "headband", False),
        )
        for headset, connected, headband, source, expected in cases:
            with self.subTest(headset=headset, headband=headband, source=source):
                self.assertIs(display_eeg(headset, connected, headband, source=source), expected)


class EegWearDisplayTests(unittest.IsolatedAsyncioTestCase):
    def cache(self):
        cache = EegAlignmentCache()
        cache.set_session("headset_rev181", "session")
        cache.set_session("headband_ble", "session")
        return cache

    async def test_worn_headset_keeps_its_eeg_when_headband_is_absent(self):
        cache = EegAlignmentCache()
        cache.set_session("headset_rev181", "session")
        result = await cache.align_result(window("headset_rev181", "signal"))
        self.assertEqual(result.algorithm_result.metrics["attention"], 20)
        self.assertEqual(result.algorithm_result.metrics["eeg"]["wave"]["left"], [1, 2, 3])
        self.assertEqual(result.algorithm_result.metrics["hr"], {"value": 69, "hrv": 44})
        self.assertIs(cache.display_eeg["headset_rev181"], True)

    async def test_both_worn_replaces_only_headset_eeg_with_headband_eeg(self):
        cache = self.cache()
        await cache.align_result(window("headband_ble", "signal"))
        result = await cache.align_result(window("headset_rev181", "signal"))
        metrics = result.algorithm_result.metrics
        self.assertEqual(metrics["attention"], 80)
        self.assertEqual(metrics["relaxation"], 81)
        self.assertEqual(metrics["eeg"]["wave"]["left"], [8, 9, 10])
        self.assertEqual(metrics["flow"], {"meditation": 83})
        self.assertEqual(metrics["sleep"], {"state": 2})
        self.assertEqual(metrics["hr"], {"value": 69, "hrv": 44})
        self.assertEqual(metrics["pressure"], 91)
        self.assertEqual(metrics["coherence"], 9)
        self.assertEqual(metrics["arousal"], 11)
        headband = await cache.align_result(window("headband_ble", "signal", start=3000))
        self.assertEqual(headband.algorithm_result.metrics["attention"], 80)
        self.assertEqual(headband.algorithm_result.metrics["hr"]["value"], 72)

    async def test_unworn_headset_hides_eeg_but_keeps_its_heart_metrics(self):
        cache = self.cache()
        await cache.align_result(window("headband_ble", "signal"))
        result = await cache.align_result(window("headset_rev181", "ff"))
        metrics = result.algorithm_result.metrics
        self.assertNotIn("eeg", metrics)
        self.assertNotIn("attention", metrics)
        self.assertNotIn("flow", metrics)
        self.assertEqual(metrics["hr"], {"value": 69, "hrv": 44})
        self.assertEqual(metrics["pressure"], 91)
        self.assertIs(cache.display_eeg["headset_rev181"], False)
        self.assertIs(cache.display_eeg["headband_ble"], True)

    async def test_both_unworn_hides_eeg_on_each_device_without_hiding_heart_metrics(self):
        cache = self.cache()
        headband = await cache.align_result(window("headband_ble", "zero"))
        headset = await cache.align_result(window("headset_rev181", "ff"))
        self.assertNotIn("eeg", headband.algorithm_result.metrics)
        self.assertEqual(headband.algorithm_result.metrics["pressure"], 32)
        self.assertNotIn("sleep", headset.algorithm_result.metrics)
        self.assertEqual(headset.algorithm_result.metrics["hr"]["hrv"], 44)
        self.assertIs(cache.display_eeg["headset_rev181"], False)
        self.assertIs(cache.display_eeg["headband_ble"], False)

    async def test_disconnect_forgets_the_other_device_and_restores_headset_eeg(self):
        cache = self.cache()
        await cache.align_result(window("headband_ble", "signal"))
        replaced = await cache.align_result(window("headset_rev181", "signal"))
        self.assertEqual(replaced.algorithm_result.metrics["attention"], 80)
        cache.set_session("headband_ble", None)
        restored = await cache.align_result(window("headset_rev181", "signal", start=3000))
        self.assertEqual(restored.algorithm_result.metrics["attention"], 20)
        self.assertNotIn("headband_ble", cache.display_eeg)

    async def test_data_event_carries_eeg_display_without_removing_heart_metrics(self):
        from neurobridge.adapters.northbound.codec import GatewayWireCodec
        from neurobridge.application.gateway import GatewayApplication

        cache = self.cache()
        hidden = await cache.align_result(window("headset_rev181", "ff"))

        class Store:
            recording_id = None

        gateway = GatewayApplication(
            type("Config", (), {"data_source": type("Source", (), {"type": "serial"})(),
                                "recording": type("Recording", (), {"subject_id": "subject"})()})(),
            store=Store(), algorithm=type("Algorithm", (), {"available": True})(), snapshots=None,
            recording_factory=lambda: None, supports_replay=False, project_window=lambda result: ({}, (), {}),
            wire=GatewayWireCodec(), initial_connection_state="connected",
        )
        gateway.status["eegDisplay"] = "hidden"
        payload = gateway.filtered_payload({}, hidden.algorithm_result.metrics, frozenset({"eeg", "hr"}))
        payload["display"] = {"eeg": gateway.status["eegDisplay"]}
        self.assertEqual(payload["display"], {"eeg": "hidden"})
        self.assertEqual(payload["algorithm"]["hr"], {"value": 69, "hrv": 44})
        self.assertEqual(payload["algorithm"]["pressure"], 91)
        self.assertNotIn("eeg", payload["algorithm"])
        self.assertNotIn("attention", payload["algorithm"])
        self.assertNotIn("eegDisplay", gateway.wire.northbound_status(gateway))
