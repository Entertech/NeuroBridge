from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_service_account_can_read_the_gateway_configuration(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        service = (ROOT / "linux" / "systemd" / "neurobridge.service").read_text(encoding="utf-8")
        self.assertIn("User=neurobridge", service)
        self.assertIn("install -o root -g neurobridge -m 0640", install_script)
        self.assertIn("chown root:neurobridge \"$config_dir/gateway.toml\"", install_script)

    def test_installer_rejects_non_ubuntu_or_non_x86_64_hosts(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        self.assertIn('uname -m) != "x86_64"', install_script)
        self.assertIn('ID:-} != "ubuntu"', install_script)
        self.assertIn('VERSION_ID:-} != "24.04"', install_script)
        self.assertIn('"--offline-bundle"', install_script)
        self.assertIn("systemctl enable neurobridge.service", install_script)

    def test_archive_export_dependencies_are_installed_on_ubuntu(self) -> None:
        bundle_builder = (ROOT / "linux" / "create-offline-bundle-ubuntu24.04.sh").read_text(encoding="utf-8")
        renderer = (ROOT / "tools" / "render-protocol-pdf.sh").read_text(encoding="utf-8")
        for package in ("pandoc", "chromium", "chromium-browser", "fonts-noto-cjk"):
            self.assertIn(package, bundle_builder)
        self.assertIn('--user-data-dir="$chrome_profile"', renderer)

    def test_installer_builds_and_installs_the_locked_algorithm_bridge(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        self.assertIn("apt-get install --no-download", install_script)
        self.assertNotIn("apt-get update", install_script)
        self.assertNotIn("git clone", install_script)
        self.assertIn('runuser -u neurobridge -- "$install_dir/linux/build-algorithm-bridge.sh"', install_script)
        self.assertIn('/usr/local/lib/neurobridge', install_script)

    def test_offline_bridge_build_never_fetches_sdk_sources(self) -> None:
        script = (ROOT / "linux" / "build-algorithm-bridge.sh").read_text(encoding="utf-8")
        self.assertNotIn("git clone", script)
        self.assertNotIn("git fetch", script)
        self.assertNotIn("prepare-algorithm-sdk", script)

    def test_dhcp_is_an_optional_isolated_service(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        bundle_builder = (ROOT / "linux" / "create-offline-bundle-ubuntu24.04.sh").read_text(encoding="utf-8")
        service = (ROOT / "linux" / "systemd" / "neurobridge-dhcp.service").read_text(encoding="utf-8")
        self.assertIn("dnsmasq", bundle_builder)
        self.assertIn("systemctl enable neurobridge-dhcp.service", install_script)
        self.assertIn("ExecCondition=", service)
        self.assertIn("--check-enabled", service)

    def test_web_assets_are_not_in_a_platform_directory(self) -> None:
        self.assertTrue((ROOT / "web" / "capture" / "index.html").is_file())
        self.assertTrue((ROOT / "web" / "b-client-test" / "index.html").is_file())
        self.assertFalse((ROOT / "mac" / "capture").exists())
