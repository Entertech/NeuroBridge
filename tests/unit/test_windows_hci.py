import unittest

from neurobridge.ble.windows_hci import WindowsHciDiagnostics


def event(packet, *, kind="2"):
    return 402, {"BIP_Type": kind, "BIP_Data": "0x" + packet.hex(),
                 "EventHeader": {"TimeStamp": 116444736010000000}}


class WindowsHciDiagnosticsTests(unittest.TestCase):
    def test_tracks_headband_and_preserves_hci_disconnect_reason(self):
        diagnostics = WindowsHciDiagnostics()
        diagnostics.track("01:23:45:67:89:AB", attempt=3)
        connection = bytes.fromhex("3E 13 01 00 2A 00 00 01 AB 89 67 45 23 01 18 00 00 00 C8 00 00")
        with self.assertLogs("neurobridge.ble.windows_hci", level="INFO") as logs:
            diagnostics.observe(event(connection))
            diagnostics.observe(event(bytes.fromhex("3E 0A 03 00 2A 00 20 00 00 00 C8 00")))
            diagnostics.observe(event(bytes.fromhex("06 04 03 2A 00 13"), kind="1"))
            diagnostics.observe(event(bytes.fromhex("05 04 00 2A 00 16")))
            diagnostics.observe(event(bytes.fromhex("05 04 00 2A 00 08")))
        self.assertIn("le_connection_complete", logs.output[0])
        self.assertIn("intervalMs=30 peripheralLatency=0 supervisionTimeoutMs=2000", logs.output[0])
        self.assertIn("le_connection_update", logs.output[1])
        self.assertIn("intervalMs=40", logs.output[1])
        self.assertIn("association=headband attempt=3 handle=0x002A", logs.output[2])
        self.assertIn("reason=0x13 reasonName=remote_user_terminated", logs.output[2])
        self.assertIn("reason=0x16 reasonName=local_host_terminated", logs.output[3])
        self.assertIn("eventUtc=1970-01-01T00:00:01.000+00:00", logs.output[3])
        self.assertIn("association=unmatched attempt=None", logs.output[4])
        self.assertNotIn("ab8967452301", "\n".join(logs.output).lower())

    def test_matches_enhanced_connection_and_ignores_acl_and_malformed_events(self):
        diagnostics = WindowsHciDiagnostics()
        diagnostics.track("1032547698BA", attempt=5)
        connection = bytes.fromhex(
            "3E 1F 0A 00 17 00 00 02 00 00 00 00 00 00 00 00 00 00 00 00 BA 98 76 54 32 10 18 00 04 00 C8 00 00"
        )
        disconnect = bytes.fromhex("05 04 00 17 00 3B")
        with self.assertLogs("neurobridge.ble.windows_hci", level="INFO") as logs:
            diagnostics.observe(event(connection))
            diagnostics.observe(event(disconnect, kind="3"))
            diagnostics.observe(event(disconnect[:-1]))
            diagnostics.observe(event(disconnect))
        self.assertEqual(len(logs.output), 2)
        self.assertIn("association=headband attempt=5 handle=0x0017", logs.output[1])
        self.assertIn("reason=0x3B reasonName=unacceptable_connection_parameters", logs.output[1])

    def test_logs_hci_parameter_request_reply_and_controller_status(self):
        diagnostics = WindowsHciDiagnostics(log_unmatched_parameters=True)
        with self.assertLogs("neurobridge.ble.windows_hci", level="INFO") as logs:
            diagnostics.observe(event(bytes.fromhex("3E 0B 06 2A 00 06 00 18 00 04 00 0A 00")))
            diagnostics.observe(event(bytes.fromhex("21 20 03 2A 00 3B"), kind="1"))
            diagnostics.observe(event(bytes.fromhex("0E 06 01 21 20 00 2A 00")))
            diagnostics.observe(event(bytes.fromhex("20 20 0E 2A 00 06 00 18 00 00 00 C8 00 00 00 04 00"), kind="1"))
            diagnostics.observe(event(bytes.fromhex("0E 06 01 20 20 00 2A 00")))
            diagnostics.observe(event(bytes.fromhex("13 20 0E 2A 00 06 00 18 00 00 00 C8 00 00 00 04 00"), kind="1"))
            diagnostics.observe(event(bytes.fromhex("0F 04 12 01 13 20")))
            diagnostics.observe(event(bytes.fromhex("3E 0A 03 3B 2A 00 00 00 00 00 00 00")))
        self.assertEqual(len(logs.output), 8)
        self.assertIn("event=le_remote_parameter_request", logs.output[0])
        self.assertIn("intervalMinMs=7.5 intervalMaxMs=30 peripheralLatency=4 supervisionTimeoutMs=100", logs.output[0])
        self.assertIn("parameterIssues=supervision_timeout_below_required_margin", logs.output[0])
        self.assertIn("event=le_remote_parameter_negative_reply", logs.output[1])
        self.assertIn("reason=0x3B", logs.output[1])
        self.assertIn("event=le_remote_parameter_negative_reply_complete", logs.output[2])
        self.assertIn("minCeMs=0 maxCeMs=2.5", logs.output[3])
        self.assertIn("event=le_remote_parameter_reply_complete", logs.output[4])
        self.assertIn("event=le_connection_update_command", logs.output[5])
        self.assertIn("handle=0x002A status=0x12", logs.output[6])
        self.assertIn("handle=0x002A status=0x3B", logs.output[7])
        self.assertNotIn("intervalMs", logs.output[7])

    def test_logs_le_signaling_pair_without_logging_sensor_data(self):
        diagnostics = WindowsHciDiagnostics(log_unmatched_parameters=True)
        request = bytes.fromhex("2A 20 10 00 0C 00 05 00 12 31 08 00 06 00 18 00 00 00 C8 00")
        response = bytes.fromhex("2A 00 0A 00 06 00 05 00 13 31 02 00 01 00")
        with self.assertLogs("neurobridge.ble.windows_hci", level="INFO") as logs:
            diagnostics.observe(event(request[:6] + b"\x04" + request[7:], kind="3"))
            diagnostics.observe(event(request[:1] + b"\x10" + request[2:], kind="3"))
            diagnostics.observe(event(request, kind="3"))
            diagnostics.observe(event(response, kind="4"))
        self.assertEqual(len(logs.output), 2)
        self.assertIn("event=l2cap_parameter_update_request", logs.output[0])
        self.assertIn("direction=controller_to_host identifier=49", logs.output[0])
        self.assertIn("event=l2cap_parameter_update_response", logs.output[1])
        self.assertIn("direction=host_to_controller identifier=49 result=0x0001 decision=rejected", logs.output[1])
