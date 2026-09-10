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


@unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell 5.1 diagnostic workflow')
class ServiceDiagnosisTests(unittest.TestCase):
    def test_service_diagnosis_handles_states_and_preserves_processes(self):
        with tempfile.TemporaryDirectory(prefix='gateway diagnosis ') as directory:
            root = Path(directory) / '中文 project'
            (root / 'windows').mkdir(parents=True)
            script = root / 'windows/diagnose-service.ps1'
            shutil.copy2(ROOT / 'windows/diagnose-service.ps1', script)
            harness = root / 'exercise.ps1'
            harness.write_text(r'''
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows\diagnose-service.ps1')
$root = $PSScriptRoot
$script:starts = 0
$script:fail = $false
$script:service = [pscustomobject]@{
    State = 'Stopped'; StartName = 'LocalSystem'; ProcessId = 0
    PathName = ('"C:\Python\python.exe" -I -S -u "' + (Join-Path $root 'windows\project_service.py') + '" host')
}
function Get-GatewayServiceSnapshot { return $script:service }
function Start-Service {
    param($Name, $ErrorAction)
    if ($Name -ne 'NeuroBridgeProject') { throw 'wrong service' }
    $script:starts++
    if ($script:fail) { throw 'synthetic start failure' }
    $script:service.State = 'Running'
}
function Get-Service {
    param($Name)
    $controller = New-Object PSObject
    $controller | Add-Member ScriptMethod WaitForStatus {
        param($state, $timeout)
        if ($state -ne 'Running' -or $timeout.TotalSeconds -ne 45) { throw 'incorrect service wait contract' }
    }
    return $controller
}
function Stop-Process { throw 'must not stop processes' }
function Stop-Service { throw 'must not stop service' }
function Remove-Item { throw 'must not delete lock/files' }
function Get-CimInstance {
    param($ClassName)
    if ($ClassName -ne 'Win32_Process') { throw 'unexpected query' }
    return [pscustomobject]@{Name='python.exe'; ProcessId=123; ParentProcessId=100; ExecutablePath='fixture-python'; CommandLine='fixture-command'}
}
$logDirectory = Join-Path $root 'custom logs'
New-Item -ItemType Directory -Path $logDirectory | Out-Null
function Get-GatewayLogInfo {
    param($ProjectRoot)
    return [pscustomobject]@{directory=$logDirectory; filename='custom.log'; level='INFO'}
}
$activeLog = Join-Path $logDirectory 'custom.log'
Set-Content -LiteralPath $activeLog -Value 'ACTIVE_RUNTIME_EVIDENCE' -Encoding UTF8
(Get-Item -LiteralPath $activeLog).LastWriteTimeUtc = [DateTime]::UtcNow.AddDays(-2)
foreach ($number in 1..3) {
    $archive = Join-Path $logDirectory ('custom.log.' + $number)
    Set-Content -LiteralPath $archive -Value ('ARCHIVE_EVIDENCE_' + $number) -Encoding UTF8
    (Get-Item -LiteralPath $archive).LastWriteTimeUtc = [DateTime]::UtcNow.AddMinutes(-$number)
}
function Expect-Rejected {
    $before = $script:starts
    $rejected = $false
    try { Start-ProjectServiceIfStopped $root | Out-Null } catch { $rejected = $true }
    if (-not $rejected -or $script:starts -ne $before) { throw 'unsafe service start' }
}
Start-ProjectServiceIfStopped $root | Out-Null
Start-ProjectServiceIfStopped $root | Out-Null
if ($script:starts -ne 1) { throw 'running service was restarted' }
$script:service.State = 'Start Pending'
Expect-Rejected
$script:service.State = 'Stopped'
$original = $script:service.PathName
$script:service.PathName = 'another project'
Expect-Rejected
$script:service.PathName = $original
$script:service.StartName = 'another account'
Expect-Rejected
$script:service.StartName = 'LocalSystem'
$saved = $script:service
$script:service = $null
Expect-Rejected
$script:service = $saved
$script:fail = $true
if ((Invoke-ServiceDiagnosis $root) -ne 1) { throw 'start failure hidden' }
$script:fail = $false
if ((Invoke-ServiceDiagnosis $root) -ne 0) { throw 'successful start failed' }
$reports = @(Get-ChildItem -LiteralPath (Join-Path $root '.runtime\diagnostics') -Filter '*.txt')
if ($reports.Count -ne 2) { throw 'reports overwritten or missing' }
$text = ($reports | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 }) -join "`n"
foreach ($expected in @('Before start', 'After start attempt', 'fixture-command', 'synthetic start failure', 'ACTIVE_RUNTIME_EVIDENCE', 'ARCHIVE_EVIDENCE_1', 'ARCHIVE_EVIDENCE_2', 'LastWriteTimeUtc')) {
    if (-not $text.Contains($expected)) { throw ('missing evidence: ' + $expected) }
}
if ($text.Contains('ARCHIVE_EVIDENCE_3')) { throw 'unbounded archive tails' }
''', encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(harness)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


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

    def test_logging_info_uses_effective_custom_path_without_exporting_config(self):
        import json
        path = helper.create_config(self.root)
        text = path.read_text(encoding='utf-8')
        text = text.replace('filename = "neurobridge.log"', 'filename = "custom.log"')
        text = text.replace(json.dumps(str(self.root / '.runtime/logs')), '"relative-logs"')
        path.write_text(text + '\n# private=DO_NOT_EXPORT\n', encoding='utf-8')
        result = helper.logging_info(self.root)
        self.assertEqual(result['directory'], str((self.root / 'relative-logs').resolve()))
        self.assertEqual(result['filename'], 'custom.log')
        self.assertEqual(result['level'], 'INFO')
        self.assertEqual(result['metricsIntervalSeconds'], 10)
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(result))
        self.assertEqual(len(result['configSha256']), 64)

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
