from dataclasses import replace
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from windows import gateway_helper as helper


ROOT = Path(__file__).resolve().parents[1]


class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gateway space ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / '中文 space'
        (self.root / 'windows').mkdir(parents=True)
        shutil.copy2(ROOT / 'windows/gateway.toml.example', self.root / 'windows/gateway.toml.example')

    def test_generate_and_preserve_operator_configuration(self):
        path = helper.create_config(self.root)
        config = helper.validate_config(path)
        self.assertEqual(config.recording.directory, self.root / '.runtime/recordings')
        self.assertEqual(config.algorithm.command, (str(self.root / '.runtime/algorithm/neurobridge_affective_bridge.exe'),))
        self.assertFalse(config.recording.replay_recording_id)
        original = path.read_text().replace('device = "auto"', 'device = "COM12"') + '\n# operator setting\n'
        path.write_text(original, encoding='utf-8')
        self.assertEqual(helper.create_config(self.root).read_text(encoding='utf-8'), original)
        self.assertEqual(helper.validate_config(path).serial.device, 'COM12')

    def test_reject_invalid_existing_policy_without_rewriting(self):
        path = helper.create_config(self.root)
        original = path.read_text()
        for changed in (
            original.replace('replay_recording_id = ""', 'replay_recording_id = "old-session"'),
            original.replace('profile = "windows_headset_local"', 'profile = "kylin_headset_local"'),
            original.replace('host = "127.0.0.1"', 'host = "0.0.0.0"'),
            original.replace('[algorithm]\nenabled = true', '[algorithm]\nenabled = false'),
        ):
            with self.subTest(changed=changed[:60]):
                path.write_text(changed, encoding='utf-8')
                with self.assertRaises(ValueError):
                    helper.create_config(self.root)
                self.assertEqual(path.read_text(encoding='utf-8'), changed)

    def test_missing_bridge_and_failed_smoke_test_are_fatal(self):
        config = helper.validate_config(helper.create_config(self.root))
        with self.assertRaisesRegex(ValueError, 'bridge missing'):
            helper.check_algorithm(config)
        Path(config.algorithm.command[0]).write_bytes(b'not executable')
        with patch.object(helper, 'smoke_test_bridge', side_effect=RuntimeError('bad bridge')):
            with self.assertRaisesRegex(RuntimeError, 'bad bridge'):
                helper.check_algorithm(config)

    def test_live_listener_and_duplicate_ports_prevent_startup(self):
        config = helper.validate_config(helper.create_config(self.root))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            config = replace(config, server=replace(config.server, port=port))
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                helper.check_ports(config)
        config = replace(config, local_ui=replace(config.local_ui, port=port))
        with self.assertRaisesRegex(ValueError, 'different'):
            helper.check_ports(config)

    def test_diagnostics_exclude_logs_config_and_recordings(self):
        path = helper.create_config(self.root)
        path.write_text(path.read_text() + '\n# credential=DO_NOT_EXPORT\n', encoding='utf-8')
        (self.root / '.runtime/logs/neurobridge.log').write_text('DO_NOT_EXPORT')
        (self.root / '.runtime/recordings/private.json').write_text('DO_NOT_EXPORT')
        with patch.object(helper, 'serial_summary', return_value=['COM3']):
            archive = helper.diagnostics(self.root)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(bundle.namelist(), ['summary.json'])
            self.assertNotIn(b'DO_NOT_EXPORT', bundle.read('summary.json'))
        self.assertTrue(archive.with_suffix('.zip.sha256').exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows OS lock and PowerShell integration')
    def test_windows_lock_blocks_duplicate_and_releases(self):
        helper.create_config(self.root)
        with helper.instance_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'already running'):
                with helper.instance_lock(self.root):
                    self.fail('duplicate lock acquired')
        with helper.instance_lock(self.root):
            pass

    @unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell 5.1 integration')
    def test_powershell_offline_start_fails_without_installing_runtime(self):
        shutil.copy2(ROOT / 'windows/setup-windows-gateway.ps1', self.root / 'windows')
        shutil.copy2(ROOT / 'windows/gateway_helper.py', self.root / 'windows')
        shutil.copy2(ROOT / 'windows/project_service.py', self.root / 'windows')
        (self.root / 'pyproject.toml').write_text('# project fixture')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(self.root / 'windows/setup-windows-gateway.ps1'),
                                 '-Action', 'start', '-Offline'], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(b'Project Python is missing', result.stdout)
        self.assertFalse((self.root / '.runtime/windows-venv').exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell 5.1 integration')
    def test_one_click_offline_prepares_config_but_refuses_missing_build_inputs(self):
        # Reuse CI's installed dependencies; this invocation must work without an index.
        shutil.copy2(ROOT / 'windows/setup-windows-gateway.ps1', self.root / 'windows')
        shutil.copy2(ROOT / 'windows/gateway_helper.py', self.root / 'windows')
        shutil.copy2(ROOT / 'windows/project_service.py', self.root / 'windows')
        shutil.copy2(ROOT / 'pyproject.toml', self.root)
        shutil.copy2(ROOT / 'requirements.lock', self.root)
        shutil.copy2(ROOT / 'sdk.lock', self.root)
        shutil.copy2(ROOT / 'windows/algorithm_build.py', self.root / 'windows')
        for relative in ('mac/algorithm_bridge', 'third_party/AffectiveCloud-Algorithm-SDK', 'third_party/NumCpp'):
            (self.root / relative).mkdir(parents=True)
            (self.root / relative / 'fixture.txt').write_text('offline test fixture')
        shutil.copytree(ROOT / 'neurobridge', self.root / 'neurobridge', ignore=shutil.ignore_patterns('__pycache__'))
        subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages',
                        str(self.root / '.runtime/windows-venv')], check=True, timeout=60)
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(self.root / 'windows/setup-windows-gateway.ps1'), '-Offline']
        first = subprocess.run(command, capture_output=True, timeout=60)
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertIn(b'Offline build input missing', first.stdout + first.stderr)
        path = helper.config_path(self.root)
        original = path.read_text(encoding='utf-8').replace('device = "auto"', 'device = "COM12"')
        path.write_text(original, encoding='utf-8')
        second = subprocess.run(command, capture_output=True, timeout=60)
        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertIn(b'Offline build input missing', second.stdout + second.stderr)
        self.assertEqual(path.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
