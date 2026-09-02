from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from neurobridge.config import load


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_kylin_single_file_entry_repairs_old_checkout_and_opens_the_menu(self) -> None:
        script_path = ROOT / "linux" / "neurobridge-kylin-bootstrap.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(os.access(script_path, os.X_OK))

        syntax = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["bash", str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Copy this one file into the root", help_result.stdout)
        self.assertIn("Do not run with sudo", help_result.stdout)

        menu_result = subprocess.run(
            ["bash", str(script_path)],
            input="0\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(menu_result.returncode, 0, menu_result.stderr)
        self.assertIn("正在打开 NeuroBridge 银河麒麟数字菜单", menu_result.stdout)
        self.assertIn("1. 新设备首次配置", menu_result.stdout)

        self.assertIn('target_branch="codex/serial-usb-transport"', script)
        self.assertIn('elif [[ -f $script_dir/../pyproject.toml ]]', script)
        self.assertIn('assistant="$project_dir/linux/setup-kylin-gateway.sh"', script)
        self.assertIn('[[ ${EUID:-$(id -u)} -ne 0 ]]', script)
        self.assertIn('[[ -n $project_dir && $project_dir != / ]]', script)
        self.assertIn('[[ -f $project_dir/pyproject.toml ]]', script)
        self.assertIn('[[ -d $project_dir/.git && ! -L $project_dir/.git ]]', script)
        self.assertIn('sudo chown -R --no-dereference "$current_uid:$current_gid" "$project_dir"', script)
        self.assertIn('find "$project_dir" -xdev ! -uid "$current_uid"', script)
        self.assertIn("本文件不会自动删除它", script)
        self.assertIn('git -C "$project_dir" pull --ff-only origin "$target_branch"', script)
        self.assertIn('git -C "$project_dir" fetch origin', script)
        self.assertIn('git -C "$project_dir" checkout -b "$target_branch" --track', script)
        self.assertIn('git -C "$project_dir" merge --ff-only "origin/$target_branch"', script)
        self.assertIn("当前存在已跟踪文件修改", script)
        self.assertNotIn('sudo git -C "$project_dir"', script)
        self.assertIn('exec bash "$assistant"', script)
        self.assertIn('kylin-bootstrap-$(date -u', script)

    def test_kylin_bootstrap_recovers_old_feature_and_master_checkouts(self) -> None:
        source = (ROOT / "linux" / "neurobridge-kylin-bootstrap.sh").read_text(encoding="utf-8")

        for starting_branch in ("codex/serial-usb-transport", "master"):
            with self.subTest(starting_branch=starting_branch), tempfile.TemporaryDirectory() as temporary_directory:
                project = Path(temporary_directory) / "NeuroBridge"
                project.mkdir()
                (project / ".git").mkdir()
                (project / "pyproject.toml").write_text("[project]\nname='bootstrap-test'\n", encoding="utf-8")
                bootstrap = project / "neurobridge-kylin-bootstrap.sh"
                bootstrap.write_text(source, encoding="utf-8")

                bin_dir = Path(temporary_directory) / "bin"
                bin_dir.mkdir()
                git_log = Path(temporary_directory) / "git.log"
                fake_git = bin_dir / "git"
                fake_git.write_text(
                    "#!/usr/bin/env bash\n"
                    "printf '%s\\n' \"$*\" >>\"$FAKE_GIT_LOG\"\n"
                    "case \"$*\" in\n"
                    "  *'symbolic-ref --quiet --short HEAD'*) printf '%s\\n' \"$FAKE_START_BRANCH\" ;;\n"
                    "  *'show-ref --verify --quiet refs/heads/codex/serial-usb-transport'*) exit 1 ;;\n"
                    "  *'pull --ff-only origin codex/serial-usb-transport'*|"
                    "*'checkout -b codex/serial-usb-transport --track origin/codex/serial-usb-transport'*)\n"
                    "    mkdir -p \"$FAKE_PROJECT/linux\"\n"
                    "    printf '%s\\n' '#!/usr/bin/env bash' 'printf '\"'\"'recovered-menu-opened\\n'\"'\"'' "
                    ">\"$FAKE_PROJECT/linux/setup-kylin-gateway.sh\"\n"
                    "    ;;\n"
                    "esac\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o755)

                environment = os.environ.copy()
                environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
                environment["FAKE_GIT_LOG"] = str(git_log)
                environment["FAKE_START_BRANCH"] = starting_branch
                environment["FAKE_PROJECT"] = str(project)
                result = subprocess.run(
                    ["bash", str(bootstrap)],
                    input="yes\n" if starting_branch == "master" else "",
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                )

                self.assertEqual(result.returncode, 0, result.stderr + "\n" + result.stdout)
                self.assertIn("recovered-menu-opened", result.stdout)
                commands = git_log.read_text(encoding="utf-8")
                if starting_branch == "master":
                    self.assertIn(
                        "fetch origin refs/heads/codex/serial-usb-transport:"
                        "refs/remotes/origin/codex/serial-usb-transport",
                        commands,
                    )
                    self.assertIn(
                        "checkout -b codex/serial-usb-transport --track "
                        "origin/codex/serial-usb-transport",
                        commands,
                    )
                else:
                    self.assertIn("pull --ff-only origin codex/serial-usb-transport", commands)
                self.assertTrue(any((project / ".runtime" / "logs").glob("kylin-bootstrap-*.log")))

    def test_kylin_bootstrap_repairs_unwritable_git_metadata_before_opening_menu(self) -> None:
        source = (ROOT / "linux" / "neurobridge-kylin-bootstrap.sh").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            project = Path(temporary_directory) / "NeuroBridge"
            linux_dir = project / "linux"
            git_dir = project / ".git"
            linux_dir.mkdir(parents=True)
            git_dir.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='permission-test'\n", encoding="utf-8")
            bootstrap = project / "neurobridge-kylin-bootstrap.sh"
            bootstrap.write_text(source, encoding="utf-8")
            assistant = linux_dir / "setup-kylin-gateway.sh"
            assistant.write_text("#!/usr/bin/env bash\nprintf 'permission-menu-opened\\n'\n", encoding="utf-8")
            index = git_dir / "index"
            index.write_bytes(b"test-index")
            git_dir.chmod(0o555)
            index.chmod(0o444)

            bin_dir = Path(temporary_directory) / "bin"
            bin_dir.mkdir()
            sudo_log = Path(temporary_directory) / "sudo.log"
            fake_sudo = bin_dir / "sudo"
            fake_sudo.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >>\"$FAKE_SUDO_LOG\"\n"
                "if [[ $1 == chown ]]; then\n"
                "  /bin/chmod -R u+rwX \"$FAKE_PROJECT\"\n"
                "  exit 0\n"
                "fi\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            fake_sudo.chmod(0o755)

            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
            environment["FAKE_SUDO_LOG"] = str(sudo_log)
            environment["FAKE_PROJECT"] = str(project)
            result = subprocess.run(
                ["bash", str(bootstrap)],
                input="yes\n",
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr + "\n" + result.stdout)
            self.assertIn("检测到项目权限异常", result.stdout)
            self.assertIn("项目权限修复完成", result.stdout)
            self.assertIn("permission-menu-opened", result.stdout)
            sudo_commands = sudo_log.read_text(encoding="utf-8")
            self.assertIn("chown -R --no-dereference", sudo_commands)
            self.assertIn(str(project), sudo_commands)
            self.assertTrue(os.access(git_dir, os.W_OK))
            self.assertTrue(os.access(index, os.W_OK))

    def test_kylin_gateway_assistant_guides_setup_and_repairs_git_permissions_safely(self) -> None:
        script_path = ROOT / "linux" / "setup-kylin-gateway.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(os.access(script_path, os.X_OK))

        syntax = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["bash", str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("First-time setup", help_result.stdout)
        self.assertIn("Repair project permissions and update source", help_result.stdout)
        self.assertIn("one decision accept yes/no", help_result.stdout)

        menu_result = subprocess.run(
            ["bash", str(script_path)],
            input="0\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(menu_result.returncode, 0, menu_result.stderr)
        self.assertIn("1. 新设备首次配置", menu_result.stdout)
        self.assertIn("2. 修复项目权限并更新代码", menu_result.stdout)
        self.assertIn("3. 检查当前 USB/串口（无需拔插）", menu_result.stdout)
        self.assertIn("请输入选项 [0-6]", menu_result.stdout)

        self.assertIn('[[ ${EUID:-$(id -u)} -ne 0 ]]', script)
        self.assertIn("Run without sudo", script)
        self.assertIn('[[ -n $root_dir && $root_dir != / ]]', script)
        self.assertIn('[[ -f $root_dir/pyproject.toml ]]', script)
        self.assertIn('[[ -d $root_dir/.git && ! -L $root_dir/.git ]]', script)
        self.assertIn('[[ -f $root_dir/linux/setup-kylin-gateway.sh ]]', script)
        self.assertLess(
            script.index("validate_project_root\n"),
            script.index('sudo chown -R --no-dereference'),
        )
        self.assertIn('sudo chown -R --no-dereference "$current_uid:$current_gid" "$root_dir"', script)
        self.assertIn('find "$root_dir" -xdev ! -uid "$current_uid"', script)
        self.assertIn('[[ ! -e $root_dir/.git/index || -w $root_dir/.git/index ]]', script)
        self.assertIn("助手不会自动删除它", script)

        self.assertIn('git -C "$root_dir" pull --ff-only', script)
        self.assertNotIn('sudo git -C "$root_dir" pull', script)
        self.assertNotIn('run_step "以普通用户更新代码" sudo', script)
        self.assertIn("为避免继续运行更新前的脚本，本助手现在退出", script)
        self.assertIn('(( result == 20 )) && exit 0', script)
        self.assertIn('show_next "bash linux/neurobridge-kylin-bootstrap.sh"', script)
        self.assertIn('sudo "$root_dir/linux/diagnose-kylin-usb-serial.sh"', script)
        self.assertIn("检查当前 USB/串口（无需拔插）", script)
        self.assertIn("--plug-cycle --timeout 60", script)
        self.assertIn("[yes/no]", script)
        self.assertIn("下一步命令：", script)
        self.assertIn('runtime_dir="$root_dir/.runtime"', script)
        self.assertIn("setup-kylin-gateway-$(date -u", script)

    def test_kylin_usb_serial_diagnosis_prompts_times_out_and_preserves_logs(self) -> None:
        script_path = ROOT / "linux" / "diagnose-kylin-usb-serial.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(os.access(script_path, os.X_OK))
        syntax = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["bash", str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--timeout SECONDS", help_result.stdout)
        self.assertIn("--plug-cycle", help_result.stdout)
        self.assertIn("Default use (no unplug required)", help_result.stdout)
        self.assertIn("--output-dir DIR", help_result.stdout)
        self.assertIn("project .runtime/diagnostics", help_result.stdout)
        self.assertIn("exit 2: no current TTY", help_result.stdout)
        self.assertIn("plug-cycle USB appeared without a new TTY", help_result.stdout)
        invalid_timeout = subprocess.run(
            ["bash", str(script_path), "--timeout", "4"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(invalid_timeout.returncode, 0)
        self.assertIn("between 5 and 600 seconds", invalid_timeout.stderr)
        self.assertIn("journalctl -k -f", script)
        self.assertIn("journalctl -u neurobridge.service -f", script)
        self.assertIn("started_at_journal=$(date '+%Y-%m-%d %H:%M:%S')", script)
        self.assertIn('--since "$started_at_journal"', script)
        self.assertIn("udevadm monitor --kernel --udev --property", script)
        self.assertIn("dmesg --follow --time-format iso", script)
        self.assertIn("$prefix-usb-interfaces.log", script)
        self.assertIn("usbguard-status", script)
        self.assertIn("if lsusb -nn >/dev/null 2>&1", script)
        self.assertIn("capture \"$prefix-lsusb\" lsusb -nn", script)
        self.assertIn("capture \"$prefix-lsusb\" lsusb", script)
        self.assertIn("lsusb -nn is unsupported", script)
        self.assertIn("capture \"$prefix-lsusb-tree\" lsusb -t", script)
        self.assertIn("result_status=usb_detected_tty_timeout", script)
        self.assertIn("result_status=usb_detection_timeout", script)
        self.assertIn("detection_mode=current", script)
        self.assertIn("result_status=current_tty_detected", script)
        self.assertIn("result_status=current_tty_not_detected", script)
        self.assertIn("目标设备将在网关启动后通过固定握手确认", script)
        self.assertIn("detectionMode=%s", script)
        self.assertIn("payloadCollected=false", script)
        self.assertIn('tar -C "$output_dir" -czf "$archive_path"', script)
        self.assertIn('sha256sum "${archive_path##*/}"', script)
        self.assertIn('artifact_uid=${SUDO_UID:-0}', script)
        self.assertIn('chown -R "$artifact_uid:$artifact_gid" "$session_dir"', script)
        self.assertIn('default_output_dir="$root_dir/.runtime/diagnostics"', script)
        self.assertIn("Output must stay under the ignored project directory", script)
        self.assertNotIn("cat /etc/neurobridge/gateway.toml", script)

    def test_kylin_runtime_diagnostics_are_self_contained_and_exclude_configuration_contents(self) -> None:
        script_path = ROOT / "linux" / "collect-kylin-runtime-diagnostics.sh"
        script = script_path.read_text(encoding="utf-8")
        syntax = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["bash", str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--output-dir DIR", help_result.stdout)
        self.assertIn("--journal-lines N", help_result.stdout)
        self.assertIn("project .runtime/diagnostics", help_result.stdout)
        self.assertIn("journalctl -u neurobridge.service", script)
        self.assertIn("/etc/kylin-release", script)
        self.assertIn("/etc/.kyinfo", script)
        self.assertIn("kylin-version-command nkvers", script)
        self.assertIn("capture_if_available uname uname -a", script)
        self.assertIn("udevadm info --query=all", script)
        self.assertIn("lsusb -t", script)
        self.assertIn("sha256sum \"$archive_name\"", script)
        self.assertIn('chmod 0600 "$archive_path" "$checksum_path"', script)
        self.assertIn("redaction-report.txt", script)
        self.assertIn('default_output_dir="$root_dir/.runtime/diagnostics"', script)
        self.assertIn('work_dir=$(mktemp -d "$output_dir/.neurobridge-kylin-diagnostics.XXXXXX")', script)
        self.assertIn('"$root_dir/.runtime/logs"', script)
        self.assertIn("Output must stay under the ignored project directory", script)
        self.assertNotIn("cat /etc/neurobridge/gateway.toml", script)
        self.assertNotIn("printenv", script)
        self.assertNotIn("/root/.ssh", script)

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
        self.assertIn("python3 rsync cmake c++", install_script)
        self.assertIn("import bleak, serial, websockets", install_script)
        self.assertIn("required by wired_b_side", install_script)
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
        quick_template = (ROOT / "config" / "ssh-operations.example.txt").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("openssh-server", preparation)
        self.assertIn("netplan.io", preparation)
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
        self.assertIn("Operator password must contain at least 6 digits", script)
        self.assertIn('[[ "$operator_password" =~ ^[0-9]{6,}$ ]]', script)
        self.assertIn("chpasswd --crypt-method SHA512", script)
        self.assertIn("ListenAddress $listen_address", script)
        self.assertIn("network_wait_script=/usr/local/lib/neurobridge/wait-for-ssh-listen-address", script)
        self.assertIn("systemd_dropin_path=$systemd_dropin_dir/10-neurobridge-listen-address.conf", script)
        self.assertIn("Wants=network-online.target", script)
        self.assertIn("After=network-online.target", script)
        self.assertIn("ExecStartPre=$network_wait_script $listen_address 90", script)
        self.assertIn("Timed out waiting ${timeout_seconds}s for SSH listen address", script)
        self.assertIn("systemctl daemon-reload", script)
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
        self.assertIn("Switch to master, fast-forward approved source", script)
        self.assertIn("master-pull-and-deploy", script)
        self.assertIn('runuser -u "$project_owner" -- /usr/bin/git -C "$source_dir" "$@"', script)
        self.assertIn('run_as_project_owner switch master', script)
        self.assertIn('run_as_project_owner pull --ff-only', script)
        self.assertIn("Managed source has local changes", script)
        self.assertIn("SSH setup requires a Git working tree", script)
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
        self.assertIn("sshd_listener_remains()", script)
        self.assertIn("ss -H -ltnp", script)
        self.assertIn("not only the requested new port", script)
        self.assertIn(r'''awk '/\("sshd",/''', script)
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
        self.assertIn("--quick [operator-ip]", wizard)
        self.assertIn("quick_mode=false", wizard)
        self.assertIn('quick_config="$root_dir/config/ssh-operations.txt"', wizard)
        self.assertIn('quick_config_template="$root_dir/config/ssh-operations.example.txt"', wizard)
        self.assertIn('install -m 0600 "$quick_config_template" "$quick_config"', wizard)
        self.assertIn('while IFS= read -r config_line', wizard)
        self.assertIn('operator_user) operator_user=$config_value', wizard)
        self.assertIn('password) operator_password=$config_value', wizard)
        self.assertIn('listen_address) listen_address=$config_value', wizard)
        self.assertIn('allow_from) operator_host=$config_value', wizard)
        self.assertIn('port) port=$config_value', wizard)
        self.assertNotIn('source "$quick_config"', wizard)
        self.assertIn("allow_from=$(python3 - \"$operator_host\"", wizard)
        self.assertIn('print(f"{address}/32")', wizard)
        self.assertIn("Quick mode accepts one operator IPv4 address", wizard)
        self.assertIn("ip -o -4 addr show scope global", wizard)
        self.assertIn("not address.is_loopback", wizard)
        self.assertIn('default_address=${private_addresses[0]}', wizard)
        self.assertIn('网关私有监听 IP${default_address:+ [$default_address]}', wizard)
        self.assertIn('listen_address=${listen_address:-$default_address}', wizard)
        self.assertIn('if [[ "$quick_mode" == false ]]', wizard)
        self.assertIn("再次输入运维账户密码", wizard)
        self.assertIn("运维账户密码（至少 6 位数字）", wizard)
        self.assertIn('[[ "$operator_password" =~ ^[0-9]{6,}$ ]]', wizard)
        self.assertIn("--operator-password-stdin", wizard)
        self.assertIn("输入 YES 确认（不区分大小写）", wizard)
        self.assertIn('[Yy][Ee][Ss])', wizard)
        self.assertIn('exec "$configure" "$@"', wizard)
        self.assertIn("operator_user=neuroops", quick_template)
        self.assertIn("password=", quick_template)
        self.assertIn("listen_address=", quick_template)
        self.assertIn("allow_from=", quick_template)
        self.assertIn("port=22", quick_template)
        self.assertIn("config/ssh-operations.txt", gitignore)

    def test_ssh_setup_supports_quick_and_full_modes(self) -> None:
        source = (ROOT / "linux" / "setup-ssh-operations.sh").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            linux_dir = temporary_root / "linux"
            config_dir = temporary_root / "config"
            bin_dir = temporary_root / "bin"
            linux_dir.mkdir()
            config_dir.mkdir()
            bin_dir.mkdir()

            wizard = linux_dir / "setup-ssh-operations.sh"
            wizard.write_text(source, encoding="utf-8")
            wizard.chmod(0o755)

            configurator = linux_dir / "configure-ssh-operations.sh"
            configurator.write_text(
                "#!/usr/bin/env bash\n"
                "IFS= read -r password\n"
                "printf 'password=%s\\n' \"$password\"\n"
                "printf 'args=%s\\n' \"$*\"\n",
                encoding="utf-8",
            )
            configurator.chmod(0o755)

            quick_config = config_dir / "ssh-operations.txt"
            quick_config.write_text(
                "operator_user=fieldops\n"
                "password=654321\n"
                "listen_address=192.168.88.10\n"
                "allow_from=192.168.88.30\n"
                "port=2222\n",
                encoding="utf-8",
            )
            quick_config.chmod(0o600)

            ip_command = bin_dir / "ip"
            ip_command.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' '2: eth0 inet 192.168.88.10/24 brd 192.168.88.255 scope global eth0'\n",
                encoding="utf-8",
            )
            ip_command.chmod(0o755)

            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
            result = subprocess.run(
                ["bash", str(wizard), "--quick", "192.168.88.20"],
                input="yes\n",
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("password=654321", result.stdout)
            self.assertIn(
                "args=--operator-user fieldops --operator-password-stdin "
                "--listen-address 192.168.88.10 --allow-from 192.168.88.20/32 --port 2222",
                result.stdout,
            )

            quick_config.chmod(0o644)
            insecure_result = subprocess.run(
                ["bash", str(wizard), "--quick"],
                input="yes\n",
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertNotEqual(insecure_result.returncode, 0)
            self.assertIn("run chmod 600", insecure_result.stderr)

            quick_config.write_text(
                "password=\n"
                "listen_address=\n"
                "allow_from=192.168.88.21\n",
                encoding="utf-8",
            )
            quick_config.chmod(0o600)
            prompted_result = subprocess.run(
                ["bash", str(wizard), "--quick"],
                input="\n\n\n123456\n123456\nyes\n",
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(prompted_result.returncode, 0, prompted_result.stderr)
            self.assertIn("password=123456", prompted_result.stdout)
            self.assertIn(
                "args=--operator-user neuroops --operator-password-stdin "
                "--listen-address 192.168.88.10 --allow-from 192.168.88.21/32 --port 22",
                prompted_result.stdout,
            )

            full_result = subprocess.run(
                ["bash", str(wizard)],
                input="\n123456\n123456\n\n192.168.88.20\n\nyes\n",
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(full_result.returncode, 0, full_result.stderr)
            self.assertIn("password=123456", full_result.stdout)
            self.assertIn(
                "args=--operator-user neuroops --operator-password-stdin "
                "--listen-address 192.168.88.10 --allow-from 192.168.88.20 --port 22",
                full_result.stdout,
            )

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

    def test_example_defaults_to_local_browser_and_keeps_wired_switch_documented(self) -> None:
        install_script = (ROOT / "linux" / "install-ubuntu.sh").read_text(encoding="utf-8")
        example = (ROOT / "config" / "gateway.toml.example").read_text(encoding="utf-8")
        self.assertIn('mode = "local_browser"', example)
        self.assertIn('host = "127.0.0.1"', example)
        self.assertIn('wired_b_side', example)
        self.assertIn('[data_source]\ntype = "serial"', example)
        self.assertIn('command_response_timeout_ms = 1000', example)
        self.assertIn('stats_interval_seconds = 10', example)
        self.assertNotIn('\ninterface = "auto"', example)
        self.assertIn('neurobridge-network-config" --config "$config_dir/gateway.toml" --apply', install_script)
        self.assertLess(
            install_script.index('if [[ ! -e "$config_dir/gateway.toml" ]]'),
            install_script.index('neurobridge-network-config" --config "$config_dir/gateway.toml" --apply'),
        )

    def test_kylin_serial_setup_is_one_command_and_preserves_a_backup(self) -> None:
        script_path = ROOT / "linux" / "setup-kylin-serial.sh"
        script = script_path.read_text(encoding="utf-8")
        syntax = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True, check=False)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn("-m neurobridge.serial_setup", script)
        self.assertIn("before-serial", script)
        self.assertIn("usermod -aG", script)
        self.assertIn("systemctl restart neurobridge.service", script)
        self.assertIn("journalctl -u neurobridge.service -f", script)
        self.assertIn('"$root_dir/venv/bin/python"', script)
        self.assertIn("for command_name in python3.11 python3", script)
        self.assertIn("--python /absolute/python", script)
        self.assertIn("runtimeReady=$runtime_ready", script)
        self.assertIn('runtime_dir="$root_dir/.runtime"', script)
        self.assertIn('config_template="$root_dir/config/gateway.project.toml.example"', script)
        self.assertIn("--system-install", script)
        self.assertIn("start-kylin-gateway.sh", script)
        self.assertIn("requiredGroups=", script)
        self.assertIn("newGroups=", script)
        self.assertIn("accountGroupMembershipChanged=", script)
        self.assertIn("sessionGroupState=", script)
        self.assertIn("Project mode --config must stay under", script)
        help_result = subprocess.run(
            ["bash", str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--python /absolute/python", help_result.stdout)

    def test_kylin_project_runtime_stays_in_ignored_checkout_directories(self) -> None:
        config = load(ROOT / "config" / "gateway.project.toml.example")
        self.assertEqual(config.data_source.type, "serial")
        self.assertEqual(config.logging.directory, Path(".runtime/logs"))
        self.assertEqual(config.recording.directory, Path(".runtime/recordings"))
        self.assertFalse(config.algorithm.enabled)

        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for ignored_directory in (".venv/", "venv/", ".runtime/", "wheelhouse/", "python-runtime/"):
            self.assertIn(ignored_directory, ignore)

        start_script = ROOT / "linux" / "start-kylin-gateway.sh"
        self.assertTrue(os.access(start_script, os.X_OK))
        syntax = subprocess.run(
            ["bash", "-n", str(start_script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["bash", str(start_script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Persistent output:", help_result.stdout)
        start_source = start_script.read_text(encoding="utf-8")
        self.assertIn('runtime_dir="$root_dir/.runtime"', start_source)
        self.assertIn("Run without sudo", start_source)
        self.assertIn("logging.directory", start_source)
        self.assertIn("recording.directory", start_source)
        self.assertIn("must remain under", start_source)
        self.assertIn("Serial permission preflight", start_source)
        self.assertIn("NEUROBRIDGE_GROUP_REEXEC", start_source)
        self.assertIn('exec sg "$activation_group" -c "$reexec_command"', start_source)
        self.assertIn("accessibleCandidates=", start_source)
        self.assertIn("discovery will continue with accessible candidates", start_source)
        self.assertIn("Log out and back in", start_source)

        python_setup = ROOT / "linux" / "setup-kylin-python.sh"
        self.assertTrue(os.access(python_setup, os.X_OK))
        syntax = subprocess.run(
            ["bash", "-n", str(python_setup)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["bash", str(python_setup), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("prepares a project-local Python 3.11", help_result.stdout)
        python_setup_source = python_setup.read_text(encoding="utf-8")
        self.assertIn('PIP_CACHE_DIR="$runtime_dir/cache/pip"', python_setup_source)
        self.assertIn('TMPDIR="$runtime_dir/tmp"', python_setup_source)
        self.assertIn('"$wheelhouse_dir"/*.whl', python_setup_source)
        self.assertIn("cpython-3.11.16+20260825-x86_64-unknown-linux-gnu-install_only.tar.gz", python_setup_source)
        self.assertIn("25844eb97cdc72cdc78addaad0969ce3b2133a4de54bfcfa4d57f8a6d095eaab", python_setup_source)
        self.assertIn("sha256sum -c", python_setup_source)
        self.assertIn("config/kylin-wheelhouse.sha256", python_setup_source)
        self.assertIn("download_portable_archive", python_setup_source)
        self.assertIn('portable_python="$portable_root/python/bin/python3"', python_setup_source)
        self.assertIn("venv-before-python311", python_setup_source)
        self.assertIn("Python environment ready", python_setup_source)
        wheel_manifest = (ROOT / "config" / "kylin-wheelhouse.sha256").read_text(encoding="utf-8")
        self.assertIn("bleak-0.19.0-py3-none-any.whl", wheel_manifest)
        self.assertIn("dbus_fast-1.95.2-cp311", wheel_manifest)
        self.assertEqual(len(wheel_manifest.splitlines()), 5)

    def test_web_assets_are_not_in_a_platform_directory(self) -> None:
        self.assertTrue((ROOT / "web" / "capture" / "index.html").is_file())
        self.assertTrue((ROOT / "web" / "b-client-test" / "index.html").is_file())
        self.assertTrue((ROOT / "web" / "b-client-test" / "runtime-config.js").is_file())
        self.assertFalse((ROOT / "mac" / "capture").exists())

    def test_capture_log_viewer_is_static_offline_and_serial_aware(self) -> None:
        capture_root = ROOT / "web" / "capture"
        html = (capture_root / "index.html").read_text(encoding="utf-8")
        javascript = (capture_root / "app.js").read_text(encoding="utf-8")
        self.assertIn('href="./styles.css"', html)
        self.assertIn('src="./app.js"', html)
        self.assertIn('type="file"', html)
        self.assertIn('id="logInput"', html)
        self.assertIn("USB 串口", html)
        self.assertIn("single_byte_0x01", javascript)
        self.assertIn("fixed_handshake", javascript)
        self.assertIn("Serial valid-frame timeout", javascript)
        self.assertIn("FileReader", javascript)
        for forbidden in ("fetch(", "XMLHttpRequest", "new WebSocket", "EventSource", "https://", "http://"):
            self.assertNotIn(forbidden, javascript)
            self.assertNotIn(forbidden, html)

    def test_diagnostic_collector_excludes_sensitive_gateway_inputs(self) -> None:
        script = (ROOT / "linux" / "collect-ubuntu-build-diagnostics.sh").read_text(encoding="utf-8")
        self.assertIn("build.log", script)
        self.assertIn("CMakeError.log", script)
        self.assertIn("neurobridge-journal", script)
        self.assertIn('chown "$archive_owner:$archive_group" "$result"', script)
        self.assertIn('chmod 0600 "$result"', script)
        self.assertNotIn('"/etc/neurobridge/gateway.toml"', script)
        self.assertNotIn('"/var/lib/neurobridge/recordings"', script)
