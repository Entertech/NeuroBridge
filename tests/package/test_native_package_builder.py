from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
