"""The MSI registers the service but owns nothing under ProgramData.

windows/service.py reads a fixed configuration path, so an installed service
cannot start until that layout exists.  Provisioning lives in the service host
rather than in WiX so it is covered by tests and works for any install shape.
"""

from pathlib import Path
import sys
import tempfile
import types
import unittest

from windows import service as host


ROOT = Path(__file__).resolve().parents[1]


class DataLayoutTests(unittest.TestCase):
    def test_provisioning_creates_the_layout_the_service_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "ProgramData" / "NeuroBridge"
            template = Path(directory) / "gateway.toml.example"
            template.write_text('profile = "windows_headset_local"\n', encoding="utf-8")

            config = data / "gateway.toml"
            self.assertTrue(host.provision_data_layout(config, template))
            self.assertTrue((data / "logs").is_dir())
            self.assertTrue((data / "recordings").is_dir())
            self.assertEqual(config.read_text(encoding="utf-8"), 'profile = "windows_headset_local"\n')

    def test_provisioning_never_replaces_an_existing_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "ProgramData" / "NeuroBridge"
            template = Path(directory) / "gateway.toml.example"
            template.write_text('profile = "windows_headset_local"\n', encoding="utf-8")
            config = data / "gateway.toml"
            config.parent.mkdir(parents=True)
            config.write_text("# operator edit\n", encoding="utf-8")

            self.assertFalse(host.provision_data_layout(config, template))
            self.assertEqual(config.read_text(encoding="utf-8"), "# operator edit\n")

    def test_a_missing_template_fails_loudly_instead_of_starting_without_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "ProgramData" / "NeuroBridge"
            with self.assertRaises(FileNotFoundError):
                host.provision_data_layout(data / "gateway.toml", Path(directory) / "absent.toml")

    def test_the_service_host_provisions_before_it_runs_the_gateway(self) -> None:
        script = (ROOT / "windows" / "service.py").read_text(encoding="utf-8")
        self.assertIn(r"C:\ProgramData\NeuroBridge\gateway.toml", script)
        # Provisioning must complete before the gateway loads its configuration,
        # and a failure must reach the event log rather than vanish.
        self.assertLess(
            script.index("provision_data_layout()"),
            script.index("self.loop = asyncio.new_event_loop()"),
        )
        self.assertIn("servicemanager.LogErrorMsg", script)

    def test_the_shipped_template_matches_the_installed_layout(self) -> None:
        """The template the service copies must point at the provisioned paths."""
        template = (ROOT / "windows" / "gateway.toml.example").read_text(encoding="utf-8")
        # TOML escapes the backslashes, so the file holds a doubled separator.
        self.assertIn(r'directory = "C:\\ProgramData\\NeuroBridge\\logs"', template)
        self.assertIn(r'directory = "C:\\ProgramData\\NeuroBridge\\recordings"', template)
        self.assertIn(
            r'command = ["C:\\Program Files\\NeuroBridge\\runtime\\neurobridge_affective_bridge.exe"]',
            template,
        )


class ServiceEntryTests(unittest.TestCase):
    """The SCM starts the host with no arguments; HandleCommandLine rejects that.

    pywin32's HandleCommandLine treats a command line that holds only the script
    name as a usage error, prints its help text and exits before any dispatcher
    runs.  That is exactly how the MSI registers the service, so the host has to
    dispatch the service itself on that path or the install fails with error 1920
    ("failed to start the service").
    """

    def setUp(self) -> None:
        self.saved_argv = sys.argv
        self.saved_modules = {
            name: sys.modules.get(name)
            for name in ("servicemanager", "win32event", "win32service", "win32serviceutil")
        }
        self.calls: list[str] = []

        def stub(name: str) -> types.ModuleType:
            module = types.ModuleType(name)
            sys.modules[name] = module
            return module

        servicemanager = stub("servicemanager")
        servicemanager.Initialize = lambda *a, **k: self.calls.append("Initialize")
        servicemanager.PrepareToHostSingle = lambda cls: self.calls.append("PrepareToHostSingle")
        servicemanager.StartServiceCtrlDispatcher = lambda: self.calls.append("StartServiceCtrlDispatcher")
        servicemanager.LogInfoMsg = lambda *a, **k: None
        servicemanager.LogErrorMsg = lambda *a, **k: None

        for name in ("win32event", "win32service"):
            stub(name)
        serviceutil = stub("win32serviceutil")
        serviceutil.ServiceFramework = type("ServiceFramework", (), {})
        serviceutil.HandleCommandLine = lambda cls, *a, **k: self.calls.append("HandleCommandLine")

    def tearDown(self) -> None:
        sys.argv = self.saved_argv
        for name, previous in self.saved_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def test_the_scm_start_path_dispatches_the_service(self) -> None:
        sys.argv = [r"C:\Program Files\NeuroBridge\windows\service.py"]
        host._service_main()
        self.assertEqual(
            self.calls,
            ["Initialize", "PrepareToHostSingle", "StartServiceCtrlDispatcher"],
        )

    def test_the_command_line_tool_still_owns_arguments(self) -> None:
        sys.argv = [r"C:\Program Files\NeuroBridge\windows\service.py", "--startup", "auto", "install"]
        host._service_main()
        self.assertEqual(self.calls, ["HandleCommandLine"])


if __name__ == "__main__":
    unittest.main()
