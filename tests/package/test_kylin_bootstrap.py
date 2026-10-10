"""The Kylin bootstrap installer is a small package that fetches its runtime.

The runtime archive is built once on a Kylin machine.  These tests cover the
half that runs everywhere else: refusing to build a package whose manifest
points nowhere, and checking that a fetched archive matches the manifest
whether it was downloaded or copied onto the machine.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_kylin_bootstrap_package", ROOT / "tools/build-kylin-bootstrap-package.py"
)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

FETCH = ROOT / "packaging/kylin/fetch-runtime.sh"


def _handler(directory: Path) -> type[http.server.BaseHTTPRequestHandler]:
    """Serve one directory over HTTP without writing access logs to stderr."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def manifest(sha: str, url: str, file_name: str = "runtime.tar.gz") -> str:
    return textwrap.dedent(f"""\
        schema_version = 1

        [runtime]
        application_version = "0.2.0"

        url = "{url}"
        sha256 = "{sha}"
        file_name = "{file_name}"
    """)


class BootstrapPackageTests(unittest.TestCase):
    def setUp(self):
        # Dependency payload fixtures only; no network or real package creation.
        self.inputs = tempfile.TemporaryDirectory()
        self.addCleanup(self.inputs.cleanup)
        def fixture(item, *args):
            path = Path(self.inputs.name) / item['filename']
            path.write_bytes(b'locked-input-fixture')
            return path
        patch = mock.patch.object(BUILDER, 'verified_input', side_effect=fixture)
        patch.start()
        self.addCleanup(patch.stop)

    def test_deb_carries_the_manifest_and_scripts_but_no_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # dpkg-deb runs inside the build's own temporary directory, which
            # is removed before build() returns, so the payload is recorded
            # while that directory still exists.
            seen = {}

            def dpkg_deb(args, **kwargs):
                source = Path(args[args.index("--root-owner-group") + 1])
                payload = source / "usr/lib/neurobridge-bootstrap"
                seen["names"] = sorted(item.name for item in payload.iterdir())
                seen["control"] = (source / "DEBIAN/control").read_text(encoding="utf-8")
                seen["postinst"] = (source / "DEBIAN/postinst").read_text(encoding="utf-8")
                Path(args[-1]).write_bytes(b"!<arch>\n" + b"\0" * 2048)

            with mock.patch.object(BUILDER.shutil, "which", return_value="dpkg-deb"), \
                    mock.patch.object(BUILDER, "run", side_effect=dpkg_deb):
                output = BUILDER.build("deb", root)

            self.assertEqual(
                seen["names"],
                ["70-neurobridge-usb-serial.rules", "bootstrap-build.sh", "bootstrap-install.sh", "build-info.txt", "diagnostic-context.sh", "export-install-logs.sh", "fetch-runtime.sh",
                 "kylin-bootstrap-inputs.toml", "kylin-runtime-manifest.toml", "platform.sh", "run-logged.sh", "source"],
            )
            self.assertGreater(output.stat().st_size, 1024)
            self.assertIn("Architecture: all", seen["control"])
            # The compiler and Eigen are declared dependencies so the install
            # builds the runtime itself instead of asking the user to run a script.
            self.assertIn("g++", seen["control"])
            # Eigen 3.3.7 is shipped in the package, so the install must not
            # depend on whichever version the distro's eigen package happens to be.
            self.assertNotIn("libeigen3-dev", seen["control"])
            self.assertIn("bootstrap-build.sh", seen["postinst"])
            # Reinstalls and upgrades deploy the package even with an existing
            # interpreter. Execution is covered by the lifecycle regressions.
            self.assertNotIn("[ ! -x", seen["postinst"])
            self.assertNotIn('"${2:-}"', seen["postinst"])
            self.assertNotIn("echo", seen["postinst"])

    def test_bundled_source_is_enough_to_build_the_runtime(self) -> None:
        """The one Kylin machine builds from the package, so the source it
        carries has to contain everything the build reads."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            present = {}

            def dpkg_deb(args, **kwargs):
                bundled = Path(args[args.index("--root-owner-group") + 1]) / "usr/lib/neurobridge-bootstrap/source"
                # The staging directory is removed when build() returns, so the
                # check has to happen while dpkg-deb is nominally running.
                for relative in (
                "pyproject.toml",
                "requirements.lock",
                "sdk.lock",
                "linux/setup-kylin-python.sh",
                "linux/setup-kylin-algorithm.sh",
                "linux/build-algorithm-bridge.sh",
                "mac/algorithm_bridge/CMakeLists.txt",
                "mac/algorithm_bridge/affective_bridge.cpp",
                "packaging/kylin/offline/cmake-3.31.6-linux-x86_64.tar.gz",
                "packaging/kylin/offline/eigen-3.3.7.tar.gz",
                "tools/build-kylin-runtime-archive.sh",
                "config/kylin-runtime-manifest.toml",
                "config/gateway.toml.example",
                "packaging/kylin/neurobridge.service",
                "packaging/kylin/export-logs.sh",
                "packaging/kylin/diagnostic-context.sh",
                "third_party/NumCpp/CMakeLists.txt",
                "neurobridge/__init__.py",
            ):
                    self.assertTrue((bundled / relative).is_file(), relative)
                self.assertTrue(any((bundled / "python-runtime").glob("*.tar.gz")))
                self.assertTrue(any((bundled / "wheelhouse").glob("*.whl")))
                self.assertFalse((bundled / "runtime").exists())
                present["checked"] = True
                Path(args[-1]).write_bytes(b"!<arch>\n" + b"\0" * 2048)

            with mock.patch.object(BUILDER.shutil, "which", return_value="dpkg-deb"), \
                    mock.patch.object(BUILDER, "run", side_effect=dpkg_deb):
                BUILDER.build("deb", root)

            self.assertTrue(present.get("checked"))

    def test_rpm_build_uses_the_same_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seen = {}

            def rpmbuild(args, **kwargs):
                topdir = Path(args[args.index("--define") + 1].split(" ", 1)[1])
                payload = topdir / "SOURCES/payload"
                seen["names"] = sorted(item.name for item in payload.iterdir())
                seen["spec"] = (topdir / "SPECS/neurobridge-bootstrap.spec").read_text(encoding="utf-8")
                rpm = topdir / "RPMS/noarch/bootstrap.rpm"
                rpm.parent.mkdir(parents=True)
                rpm.write_bytes(bytes.fromhex("edabeedb") + b"\0" * 2048)

            with mock.patch.object(BUILDER.shutil, "which", return_value="rpmbuild"), \
                    mock.patch.object(BUILDER, "run", side_effect=rpmbuild):
                BUILDER.build("rpm", root)

            self.assertEqual(
                seen["names"],
                ["70-neurobridge-usb-serial.rules", "bootstrap-build.sh", "bootstrap-install.sh", "build-info.txt", "diagnostic-context.sh", "export-install-logs.sh", "fetch-runtime.sh",
                 "kylin-bootstrap-inputs.toml", "kylin-runtime-manifest.toml", "platform.sh", "run-logged.sh", "source"],
            )
            self.assertNotIn("eigen3-devel", seen["spec"])
            self.assertIn("%preun", seen["spec"])
            self.assertIn("%postun", seen["spec"])


class StageVenvPackagesTests(unittest.TestCase):
    def test_wheels_are_copied_and_editable_installs_are_skipped(self) -> None:
        if not shutil.which("bash"):
            self.skipTest("bash is required")
        script = ROOT / "tools/stage-venv-packages.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv = root / "venv"
            runtime = root / "runtime"
            (venv / "serial").mkdir(parents=True)
            (venv / "serial/__init__.py").write_text("serial\n")
            (venv / "websockets").mkdir()
            (venv / "websockets/__init__.py").write_text("websockets\n")
            (venv / "__pycache__").mkdir()
            (venv / "__pycache__/stale.pyc").write_bytes(b"pyc")
            (venv / "neurobridge-0.1.0.dist-info").write_text("editable: /checkout/neurobridge\n")
            (venv / "pip.pth").write_text("import pip\n")
            runtime.mkdir()

            result = subprocess.run(["bash", str(script), str(venv), str(runtime)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((runtime / "serial/__init__.py").read_text(), "serial\n")
            self.assertTrue((runtime / "websockets/__init__.py").is_file())
            self.assertFalse((runtime / "__pycache__").exists())
            self.assertFalse((runtime / "neurobridge-0.1.0.dist-info").exists())
            self.assertFalse((runtime / "pip.pth").exists())


class FetchRuntimeTests(unittest.TestCase):
    def write_archive(self, directory: Path, name: str, content: bytes) -> tuple[Path, str]:
        path = directory / name
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def write_manifest(self, directory: Path, sha: str, url: str, file_name: str) -> Path:
        path = directory / "manifest.toml"
        path.write_text(manifest(sha, url, file_name), encoding="utf-8")
        return path

    def run_fetch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(FETCH), *args], text=True, capture_output=True, check=False
        )

    def test_local_archive_is_accepted_only_when_the_digest_matches(self) -> None:
        shell = shutil.which("bash")
        sha256sum = shutil.which("sha256sum")
        if not shell or not sha256sum:
            self.skipTest("bash and sha256sum are required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, sha = self.write_archive(root, "runtime.tar.gz", b"runtime bytes")
            manifest_path = self.write_manifest(root, sha, "", "runtime.tar.gz")
            destination = root / "dest"

            accepted = self.run_fetch(
                "--manifest", str(manifest_path), "--destination", str(destination),
                "--local-archive", str(archive),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            fetched = Path(accepted.stdout.strip())
            self.assertEqual(fetched.read_bytes(), b"runtime bytes")

            (root / "tampered.tar.gz").write_bytes(b"other bytes")
            rejected = self.run_fetch(
                "--manifest", str(manifest_path), "--destination", str(root / "dest-bad"),
                "--local-archive", str(root / "tampered.tar.gz"),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("sha256 mismatch", rejected.stderr)

    def test_download_is_refused_when_the_manifest_has_no_url(self) -> None:
        if not shutil.which("bash"):
            self.skipTest("bash is required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.write_manifest(root, "cd" * 32, "", "runtime.tar.gz")
            result = self.run_fetch("--manifest", str(manifest_path), "--destination", str(root / "dest"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no download URL", result.stderr)

    def test_download_is_discarded_when_the_digest_does_not_match(self) -> None:
        if not shutil.which("bash") or not shutil.which("sha256sum") or not shutil.which("curl"):
            self.skipTest("bash, sha256sum and curl are required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            served = root / "served"
            served.mkdir()
            self.write_archive(served, "runtime.tar.gz", b"downloaded bytes")
            server = http.server.HTTPServer(("127.0.0.1", 0), _handler(served))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/runtime.tar.gz"
                # The server returns real bytes, but the manifest expects a
                # different digest, so the download must be thrown away.
                manifest_path = self.write_manifest(root, "ab" * 32, url, "runtime.tar.gz")
                result = self.run_fetch(
                    "--manifest", str(manifest_path), "--destination", str(root / "dest")
                )
            finally:
                server.shutdown()
                server.server_close()
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("sha256 mismatch", result.stderr)
            self.assertFalse((root / "dest" / "runtime.tar.gz").exists())

    def test_manifest_with_an_unsafe_file_name_is_rejected(self) -> None:
        if not shutil.which("bash"):
            self.skipTest("bash is required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.write_manifest(root, "ab" * 32, "", "../runtime.tar.gz")
            result = self.run_fetch(
                "--manifest", str(manifest_path), "--destination", str(root / "dest"),
                "--local-archive", str(root / "missing.tar.gz"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not safe", result.stderr)


class BootstrapInstallScriptTests(unittest.TestCase):
    def test_install_script_has_valid_shell_syntax(self) -> None:
        shell = shutil.which("bash")
        if not shell:
            self.skipTest("bash is required")
        script = ROOT / "packaging/kylin/bootstrap-install.sh"
        self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
        result = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        fetch = subprocess.run([shell, "-n", str(FETCH)], capture_output=True, text=True, check=False)
        self.assertEqual(fetch.returncode, 0, fetch.stderr)
        archive = subprocess.run(
            [shell, "-n", str(ROOT / "tools/build-kylin-runtime-archive.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(archive.returncode, 0, archive.stderr)
        build = subprocess.run(
            [shell, "-n", str(ROOT / "packaging/kylin/bootstrap-build.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(build.returncode, 0, build.stderr)


if __name__ == "__main__":
    unittest.main()
