from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_native_package", ROOT / "tools/build-native-package.py")
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class NativePackageBuilderTests(unittest.TestCase):
    def runtime(self, root: Path, target: str) -> Path:
        runtime = root / "runtime"
        if target.startswith("windows"):
            runtime.mkdir()
            (runtime / "python.exe").write_bytes(b"python")
            (runtime / "neurobridge_affective_bridge.exe").write_bytes(b"bridge")
        else:
            (runtime / "bin").mkdir(parents=True)
            (runtime / "bin/python").write_bytes(b"python")
            (runtime / "bin/neurobridge_affective_bridge").write_bytes(b"bridge")
        return runtime

    def test_source_payload_is_staged_with_bundled_runtime(self) -> None:
        target = BUILDER.target_for("kylin-server-x86_64-deb")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self.runtime(root, target["id"])
            stage = root / "stage"
            BUILDER.copy_source(stage, target, runtime)
            BUILDER.runtime_requirements(target, runtime)
            self.assertTrue((stage / "opt/neurobridge/neurobridge/__init__.py").is_file())
            self.assertTrue((stage / "opt/neurobridge/runtime/bin/python").is_file())
            self.assertTrue((stage / "opt/neurobridge/packaging/neurobridge.service").is_file())

    def test_windows_service_uses_installed_script_path(self) -> None:
        target = BUILDER.target_for("windows-10-x86_64-msi")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self.runtime(root, target["id"])
            stage = root / "stage"
            BUILDER.copy_source(stage, target, runtime)
            source_root = stage / "opt/neurobridge"
            _, components = BUILDER.wix_directory_tree(source_root, source_root, "INSTALLFOLDER")
            service = next(item for item in components if "ServiceInstall" in item)
            self.assertIn('Arguments="&quot;[#SERVICE_SCRIPT]&quot;"', service)
            rendered = "\n".join(components)
            self.assertIn('File Id="SERVICE_SCRIPT"', rendered)
            self.assertNotIn("Win64=", rendered)
            self.assertNotIn("-m windows.service", service)

    def test_missing_runtime_is_rejected(self) -> None:
        target = BUILDER.target_for("windows-10-x86_64-msi")
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            with self.assertRaisesRegex(ValueError, "python.exe"):
                BUILDER.runtime_requirements(target, runtime)

    def test_source_reference_is_immutable_and_has_sha256(self) -> None:
        reference = BUILDER.source_reference("a" * 40)[0]
        self.assertEqual(reference["url"], "https://github.com/Entertech/NeuroBridge/commit/" + "a" * 40)
        self.assertRegex(reference["sha256"], r"^[0-9a-f]{64}$")

    def test_maintainer_scripts_only_act_on_the_unit_the_package_installed(self) -> None:
        """A source deployment owns the same unit path, so the guard is load-bearing."""
        target = BUILDER.target_for("kylin-server-x86_64-deb")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            BUILDER.deb_scripts(stage, target)
            prerm = (stage / "DEBIAN/prerm").read_text(encoding="utf-8")
            postrm = (stage / "DEBIAN/postrm").read_text(encoding="utf-8")

            for name, script in (("prerm", prerm), ("postrm", postrm)):
                self.assertIn("unit_is_ours", script, name)
                self.assertIn("ExecStart=/opt/neurobridge/runtime/bin/python", script, name)
                shell = shutil.which("sh")
                if shell:
                    syntax = subprocess.run(
                        [shell, "-n"], input=script, text=True, capture_output=True, check=False
                    )
                    self.assertEqual(syntax.returncode, 0, f"{name}: {syntax.stderr}")

            # Stopping is verified rather than assumed: the unit restarts itself
            # every three seconds, so a stop still in flight can resurrect it.
            self.assertIn("stop_our_unit", prerm)
            self.assertIn("systemctl is-active", prerm)
            self.assertIn("systemctl kill", prerm)
            # Removing the unit is guarded; the old unconditional delete is gone.
            self.assertIn("remove_our_unit", postrm)
            self.assertNotIn("rm -f /etc/systemd/system/neurobridge.service", postrm)
            self.assertIn("remove|purge", postrm)
            # A package must never carry a helper it does not call.
            self.assertNotIn("remove_our_unit()", prerm)
            self.assertNotIn("stop_our_unit()", postrm)

            spec = BUILDER.rpm_spec(target, root / "rpmbuild").read_text(encoding="utf-8")
            preun = spec[spec.index("%preun"):spec.index("%postun")]
            postun = spec[spec.index("%postun"):spec.index("%files")]
            self.assertIn("unit_is_ours", preun)
            self.assertIn("stop_our_unit", preun)
            self.assertIn("systemctl is-active", preun)
            self.assertIn("unit_is_ours", postun)
            self.assertIn("remove_our_unit", postun)
            self.assertNotIn("systemctl disable --now", postun)

    def test_packages_replace_the_installers_shipped_in_run_37751485418(self) -> None:
        """The installers from that run are already in the field at version 0.2.0.

        A later build of the same version must replace them rather than install
        beside them, and the replacement must not stop the running gateway.
        """
        shipped_version = "0.2.0"
        self.assertEqual(BUILDER.APPLICATION_VERSION, shipped_version)

        # Windows MSI.  The upgrade code is derived, not stored, so a future
        # edit that rewords the URL would silently stop detecting the shipped
        # product.  Same-version replacement is off unless asked for.
        target = BUILDER.target_for("windows-10-x86_64-msi")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            BUILDER.copy_source(stage, target, self.runtime(root, target["id"]))
            output = root / "neurobridge.msi"
            log = root / "build.log"
            real_which = BUILDER.shutil.which
            real_command = BUILDER.command
            BUILDER.shutil.which = lambda name: name if name == "wix" else real_which(name)
            BUILDER.command = lambda *args, **kwargs: ""
            try:
                BUILDER.write_wix_msi(stage, target, output, log)
            finally:
                BUILDER.shutil.which = real_which
                BUILDER.command = real_command
            wxs = (stage / "neurobridge.wxs").read_text(encoding="utf-8")
            upgrade_code = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Entertech/NeuroBridge"))
            self.assertIn(f'Version="{shipped_version}"', wxs)
            self.assertIn(f'UpgradeCode="{upgrade_code}"', wxs)
            self.assertIn('AllowSameVersionUpgrades="yes"', wxs)
            self.assertNotIn("ProductCode=", wxs)

        # Windows EXE (Burn).  The bundle detects its predecessor by its own
        # upgrade code; MajorUpgrade only covers the MSI inside it.
        with tempfile.TemporaryDirectory() as directory:
            bundle_path = Path(directory) / "neurobridge-bundle.wxs"
            msi = Path(directory) / "neurobridge.msi"
            msi.write_bytes(b"")
            real_which = BUILDER.shutil.which
            BUILDER.shutil.which = lambda name: name if name == "wix" else real_which(name)
            real_command = BUILDER.command
            BUILDER.command = lambda *args, **kwargs: ""
            try:
                BUILDER.write_wix_bundle(msi, Path(directory) / "neurobridge.exe", Path(directory) / "build.log")
            finally:
                BUILDER.shutil.which = real_which
                BUILDER.command = real_command
            bundle = bundle_path.read_text(encoding="utf-8")
        bundle_upgrade = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Entertech/NeuroBridge/bundle"))
        self.assertIn(f'Version="{shipped_version}"', bundle)
        self.assertIn(f'UpgradeCode="{bundle_upgrade}"', bundle)
        self.assertIn(f'<RelatedBundle Action="Upgrade" Id="{bundle_upgrade}" />', bundle)

        # Kylin.  The package name is what apt/dnf match on, and prerm must let
        # an upgrade through without stopping the service the new package keeps.
        for edition in ("desktop", "server"):
            target = BUILDER.target_for(f"kylin-{edition}-x86_64-deb")
            self.assertEqual(BUILDER.deb_control(target).splitlines()[0], f"Package: neurobridge-{edition}")
            self.assertIn(f"Version: {shipped_version}", BUILDER.deb_control(target))
            with tempfile.TemporaryDirectory() as directory:
                stage = Path(directory) / "stage"
                stage.mkdir()
                BUILDER.deb_scripts(stage, target)
                prerm = (stage / "DEBIAN/prerm").read_text(encoding="utf-8")
            guard, _, body = prerm.partition("unit=")
            self.assertIn("upgrade|failed-upgrade|abort-upgrade) exit 0", guard)
            self.assertLess(prerm.index("exit 0"), prerm.index("unit="))
            self.assertIn("stop_our_unit", body)

    def test_payloads_ship_the_platform_configuration_template(self) -> None:
        # The installed service copies this template to its data directory, and
        # the profile it declares selects the serial source and algorithm path.
        for target_id, expected in (
            ("windows-10-x86_64-msi", 'profile = "windows_headset_local"'),
            ("kylin-server-x86_64-deb", 'profile = "kylin_headset_local"'),
        ):
            with self.subTest(target=target_id):
                target = BUILDER.target_for(target_id)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    runtime = self.runtime(root, target_id)
                    stage = root / "stage"
                    BUILDER.copy_source(stage, target, runtime)
                    template = (stage / "opt/neurobridge/gateway.toml.example").read_text(encoding="utf-8")
                    self.assertIn(expected, template)
                    if target_id.startswith("windows"):
                        self.assertIn('candidate_types = ["COM"]', template)
                        self.assertIn(r'directory = "C:\\ProgramData\\NeuroBridge\\logs"', template)
                        self.assertNotIn("ttyACM", template)
                        self.assertNotIn("/opt/neurobridge", template)

    def test_payloads_ship_the_log_exporter(self) -> None:
        # The Kylin payload copies packaging/kylin/ to opt/neurobridge/kylin/,
        # while the Windows payload ships the windows/ directory as-is.
        for target_id, relative in (
            ("kylin-server-x86_64-deb", "opt/neurobridge/kylin/export-logs.sh"),
            ("windows-10-x86_64-msi", "opt/neurobridge/windows/export-logs.ps1"),
        ):
            with self.subTest(target=target_id):
                target = BUILDER.target_for(target_id)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    runtime = self.runtime(root, target_id)
                    stage = root / "stage"
                    BUILDER.copy_source(stage, target, runtime)
                    exporter = stage / relative
                    self.assertTrue(exporter.is_file(), f"{relative} must ship inside {target_id}")
                    if target_id.startswith("kylin"):
                        self.assertTrue(os.access(exporter, os.X_OK))


if __name__ == "__main__":
    unittest.main()
