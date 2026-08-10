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
        self.assertIn('[[ $# -eq 0 ]]', install_script)
        self.assertIn("systemctl enable neurobridge.service", install_script)

    def test_one_command_update_uses_only_the_existing_checkout(self) -> None:
        script = (ROOT / "linux" / "update-ubuntu.sh").read_text(encoding="utf-8")
        self.assertIn('exec sudo --preserve-env=PATH bash "$0"', script)
        self.assertIn('bash "$root_dir/linux/install-ubuntu.sh"', script)
        self.assertNotIn("git pull", script)
        self.assertNotIn("git fetch", script)
        self.assertNotIn("git clone", script)

    def test_online_environment_preparation_is_separate_from_offline_deployment(self) -> None:
        script = (ROOT / "linux" / "prepare-ubuntu24.04-environment.sh").read_text(encoding="utf-8")
        self.assertIn("apt-get update", script)
        self.assertIn("apt-get install", script)
        self.assertIn('pip install -r "$root_dir/requirements.lock"', script)
        self.assertIn('python3 -m venv "$install_dir/venv"', script)
        self.assertIn("Ubuntu 24.04", script)

    def test_ssh_operations_are_explicitly_enabled_and_password_authenticated(self) -> None:
        preparation = (ROOT / "linux" / "prepare-ubuntu24.04-environment.sh").read_text(encoding="utf-8")
        script = (ROOT / "linux" / "configure-ssh-operations.sh").read_text(encoding="utf-8")
        wizard = (ROOT / "linux" / "setup-ssh-operations.sh").read_text(encoding="utf-8")
        self.assertIn("openssh-server", preparation)
        self.assertIn("systemctl disable --now ssh.socket", preparation)
        self.assertIn("systemctl disable --now ssh.service", preparation)
        self.assertLess(
            preparation.index("systemctl disable --now ssh.socket"),
            preparation.index("systemctl disable --now ssh.service"),
        )
        self.assertIn("--operator-password-stdin", script)
        self.assertIn("PermitRootLogin no", script)
        self.assertIn("PasswordAuthentication yes", script)
        self.assertIn("PubkeyAuthentication no", script)
        self.assertIn("AuthenticationMethods password", script)
        self.assertIn("Operator password must contain exactly 6 digits", script)
        self.assertIn('[[ "$operator_password" =~ ^[0-9]{6}$ ]]', script)
        self.assertIn("chpasswd --crypt-method SHA512", script)
        self.assertIn("ListenAddress $listen_address", script)
        self.assertIn("Match User $operator_user Address *,!$allow_from", script)
        self.assertIn("print(allowed.with_prefixlen)", script)
        self.assertIn("AllowUsers $operator_user", script)
        self.assertIn("00-neurobridge-operations.conf", script)
        self.assertIn("unexpected listening ports", script)
        self.assertIn("neurobridge-ops-status", script)
        self.assertIn("neurobridge-ops-logs", script)
        self.assertIn("logs [--follow|-f|--lines N]", script)
        self.assertIn("neurobridge-ops-update", script)
        self.assertIn("neurobridge-ops-command", script)
        self.assertIn("neurobridge-ops-audit", script)
        self.assertIn("run_and_audit()", script)
        self.assertIn("result=$result", script)
        self.assertIn("neurobridge-ops project", script)
        self.assertIn("exec sudo -- /usr/local/sbin/neurobridge-ops-command audit", script)
        self.assertNotIn("/usr/bin/systemctl start neurobridge.service, /usr/bin/systemctl", script)
        self.assertNotIn("/srv/neurobridge-release", script)
        self.assertIn("project_dir=$operator_home/NeuroBridge", script)
        self.assertIn("Project working tree must be owned by $project_owner", script)
        self.assertIn('printf \'source_dir=%q\\n\' "$project_dir"', script)
        self.assertIn('project_owner=${SUDO_USER:-}', script)
        self.assertIn('chown -R --no-dereference "$operator_user:$operator_user" "$project_dir"', script)
        self.assertNotIn("! -user root", script)
        self.assertIn("neurobridge-ops update", script)
        self.assertIn('/usr/bin/systemctl "$1" neurobridge.service', script)
        self.assertIn("operator_shadow_before", script)
        self.assertIn("rollback()", script)
        self.assertIn("ssh_socket_was_enabled", script)
        self.assertIn("ssh_socket_was_active", script)
        self.assertIn("systemctl cat ssh.socket", script)
        self.assertIn("systemctl disable --now ssh.socket", script)
        self.assertIn("stop_ssh_service_processes()", script)
        self.assertIn("systemctl kill --kill-who=all --signal=TERM ssh.service", script)
        self.assertIn("systemctl kill --kill-who=all --signal=KILL ssh.service", script)
        self.assertIn('ss -H -ltn "sport = :$port"', script)
        self.assertIn("Run this setup from the local gateway console", script)
        self.assertIn("transaction_active=true", script)
        self.assertLess(
            script.index("systemctl disable --now ssh.socket"),
            script.index('if ! systemctl enable ssh.service'),
        )
        self.assertLess(
            script.index('if ! systemctl enable ssh.service'),
            script.index('printf \'%s:%s\\n\' "$operator_user" "$operator_password" | chpasswd --crypt-method SHA512'),
        )
        self.assertLess(
            script.index('printf \'%s:%s\\n\' "$operator_user" "$operator_password" | chpasswd --crypt-method SHA512'),
            script.index('install -o root -g root -m 0440 "$sudoers_tmp" "$sudoers_path"'),
        )
        self.assertIn("NeuroBridge SSH 运维一键配置", wizard)
        self.assertIn("ip -o -4 addr show scope global", wizard)
        self.assertIn("not address.is_loopback", wizard)
        self.assertIn('default_address=${private_addresses[0]}', wizard)
        self.assertIn('网关私有监听 IP${default_address:+ [$default_address]}', wizard)
        self.assertIn('listen_address=${listen_address:-$default_address}', wizard)
        self.assertIn("再次输入运维账户密码", wizard)
        self.assertIn("运维账户密码（6 位数字）", wizard)
        self.assertIn('[[ "$operator_password" =~ ^[0-9]{6}$ ]]', wizard)
        self.assertIn("--operator-password-stdin", wizard)
        self.assertIn("输入 YES 确认", wizard)

    def test_installer_builds_and_installs_the_locked_algorithm_bridge(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        self.assertIn("--exclude venv", install_script)
        self.assertIn("PIP_NO_INDEX=1", install_script)
        self.assertIn("--no-build-isolation", install_script)
        self.assertNotIn("apt-get", install_script)
        self.assertNotIn("git clone", install_script)
        self.assertNotIn("git fetch", install_script)
        self.assertIn('runuser -u neurobridge -- "$install_dir/linux/build-algorithm-bridge.sh"', install_script)
        self.assertIn('/usr/local/lib/neurobridge', install_script)

    def test_vendored_bridge_build_never_fetches_sdk_sources(self) -> None:
        script = (ROOT / "linux" / "build-algorithm-bridge.sh").read_text(encoding="utf-8")
        self.assertNotIn("git clone", script)
        self.assertNotIn("git fetch", script)
        self.assertNotIn("prepare-algorithm-sdk", script)
        self.assertIn('sdk_dir="$sdk_root/AffectiveCloud-Algorithm-SDK"', script)
        self.assertIn('numcpp_dir="$sdk_root/NumCpp"', script)
        self.assertEqual(script.count("-DNUMCPP_NO_USE_BOOST=ON"), 2)
        self.assertIn('rm -rf "$build_root"', script)
        self.assertIn('tee "$build_log"', script)
        self.assertTrue((ROOT / "third_party" / "AffectiveCloud-Algorithm-SDK" / "cpp" / "package" / "CMakeLists.txt").is_file())
        self.assertTrue((ROOT / "third_party" / "NumCpp" / "CMakeLists.txt").is_file())

    def test_vendored_sdk_header_includes_its_numeric_limits_dependency(self) -> None:
        header = (
            ROOT
            / "third_party"
            / "AffectiveCloud-Algorithm-SDK"
            / "cpp"
            / "package"
            / "include"
            / "DSPBCG.h"
        ).read_text(encoding="utf-8")
        self.assertIn("#include <limits>", header)
        self.assertIn("std::numeric_limits<double>", header)

    def test_vendored_sdk_fft_tool_includes_its_cstring_dependency(self) -> None:
        source = (
            ROOT
            / "third_party"
            / "AffectiveCloud-Algorithm-SDK"
            / "cpp"
            / "package"
            / "source"
            / "BASIC"
            / "TOOL"
            / "FFTTool.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("#include <cstring>", source)
        self.assertIn("std::memset(", source)

    def test_vendored_sdk_wavelet_tool_includes_its_standard_dependencies(self) -> None:
        source = (
            ROOT
            / "third_party"
            / "AffectiveCloud-Algorithm-SDK"
            / "cpp"
            / "package"
            / "source"
            / "BASIC"
            / "TOOL"
            / "WaveletTool.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("#include <cctype>", source)
        self.assertIn("#include <cstring>", source)
        self.assertIn("#include <stdexcept>", source)
        self.assertIn("std::strcmp(", source)
        self.assertIn("std::invalid_argument", source)

    def test_vendored_sdk_public_api_includes_its_fixed_width_integer_dependency(self) -> None:
        header = (
            ROOT
            / "third_party"
            / "AffectiveCloud-Algorithm-SDK"
            / "cpp"
            / "package"
            / "public"
            / "Affective.h"
        ).read_text(encoding="utf-8")
        self.assertIn("#include <cstdint>", header)
        self.assertIn("#include <vector>", header)
        self.assertIn("std::vector<std::uint8_t>", header)

    def test_dhcp_is_an_optional_isolated_service(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        service = (ROOT / "linux" / "systemd" / "neurobridge-dhcp.service").read_text(encoding="utf-8")
        self.assertIn("systemctl enable neurobridge-dhcp.service", install_script)
        self.assertIn("ExecCondition=", service)
        self.assertIn("--check-enabled", service)

    def test_web_assets_are_not_in_a_platform_directory(self) -> None:
        self.assertTrue((ROOT / "web" / "capture" / "index.html").is_file())
        self.assertTrue((ROOT / "web" / "b-client-test" / "index.html").is_file())
        self.assertFalse((ROOT / "mac" / "capture").exists())

    def test_diagnostic_collector_excludes_sensitive_gateway_inputs(self) -> None:
        script = (ROOT / "linux" / "collect-ubuntu-build-diagnostics.sh").read_text(encoding="utf-8")
        self.assertIn("build.log", script)
        self.assertIn("CMakeError.log", script)
        self.assertIn("neurobridge-journal", script)
        self.assertIn('chown "$archive_owner:$archive_group" "$result"', script)
        self.assertIn('chmod 0600 "$result"', script)
        self.assertNotIn('"/etc/neurobridge/gateway.toml"', script)
        self.assertNotIn('"/var/lib/neurobridge/recordings"', script)
