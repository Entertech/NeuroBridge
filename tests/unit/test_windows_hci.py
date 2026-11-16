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
