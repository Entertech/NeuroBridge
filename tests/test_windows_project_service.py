import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen
from types import SimpleNamespace
from unittest.mock import patch

from windows import project_service as service


class ServicePolicyTests(unittest.TestCase):
    def test_preference_defaults_and_persists_explicit_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(service.autostart_enabled(root))
            service.save_preference(root, False)
            self.assertFalse(service.autostart_enabled(root))
            service.save_preference(root, True)
            self.assertTrue(service.autostart_enabled(root))
            service.preference_path(root).write_text('{"enabled":"false"}')
            with self.assertRaises(ValueError):
                service.autostart_enabled(root)

    def test_owner_check_rejects_other_installations_and_accounts(self):
        root = Path('project with spaces')
        python = root / 'python.exe'
        command = service.service_command(root, python)
        self.assertTrue(command.startswith('"'))
        config = [None] * 9
        config[3], config[7] = command, 'LocalSystem'
        service.verify_owner(config, root, python)
        config[3] = 'another-service.exe'
        with self.assertRaisesRegex(ValueError, 'another installation'):
            service.verify_owner(config, root, python)
        config[3], config[7] = command, 'other-account'
        with self.assertRaisesRegex(ValueError, 'account'):
            service.verify_owner(config, root, python)


class ServiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_waits_for_gateway_cleanup(self):
        stopped = threading.Event()
        cleaned = asyncio.Event()
        started = asyncio.Event()

        async def gateway(path):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        config = SimpleNamespace(local_ui=SimpleNamespace(port=1), pipeline=SimpleNamespace(shutdown_timeout_ms=1000))
        with patch.object(service.asyncio, 'open_connection', side_effect=OSError('not ready')):
            task = asyncio.create_task(service.serve_until_stopped(config, stopped, lambda: None, gateway_run=gateway))
            await started.wait()
            stopped.set()
            await asyncio.wait_for(task, 2)
        self.assertTrue(cleaned.is_set())

    async def test_startup_failure_propagates(self):
        async def gateway(path):
            raise RuntimeError('bad startup')
        config = SimpleNamespace(local_ui=SimpleNamespace(port=1), pipeline=SimpleNamespace(shutdown_timeout_ms=1000))
        with patch.object(service.asyncio, 'open_connection', side_effect=OSError('not ready')):
            with self.assertRaisesRegex(RuntimeError, 'bad startup'):
                await service.serve_until_stopped(config, threading.Event(), lambda: None, gateway_run=gateway)

    async def test_startup_timeout_cleans_up_without_reporting_running(self):
        cleaned = asyncio.Event()
        async def gateway(path):
            try:
                await asyncio.Future()
            finally:
                cleaned.set()
        config = SimpleNamespace(local_ui=SimpleNamespace(port=1), pipeline=SimpleNamespace(shutdown_timeout_ms=1000))
        with patch.object(service.asyncio, 'open_connection', side_effect=OSError('not ready')):
            with self.assertRaisesRegex(TimeoutError, 'did not become ready'):
                await service.serve_until_stopped(config, threading.Event(),
                    lambda: self.fail('Reported ready without a listener'), gateway_run=gateway, startup_timeout=0.1)
        self.assertTrue(cleaned.is_set())


@unittest.skipUnless(sys.platform == 'win32', 'Requires Windows SCM and administrator CI runner')
class NativeServiceTests(unittest.TestCase):
    def test_register_start_reuse_stop_and_manual_preference(self):
        import win32api
        import win32con
        import win32event
        import win32service as ws
        from windows.gateway_helper import create_config
        from windows.algorithm_build import ROOT
        # Exercise the real host against real built algorithm, without a USB device.
        bridge = ROOT / '.runtime/algorithm/neurobridge_affective_bridge.exe'
        if not bridge.exists():
            self.skipTest('Run after native algorithm build')
        name = 'NeuroBridgeProjectCI'
        # Isolated checkout with the service name changed only in the temporary fixture.
        with tempfile.TemporaryDirectory(prefix='neurobridge service ') as directory:
            # PowerShell/Python resolve RUNNER~1 to runneradmin. Use the same
            # canonical path when registering and checking service ownership.
            root = Path(directory).resolve()
            shutil.copy2(ROOT / 'pyproject.toml', root / 'pyproject.toml')
            shutil.copytree(ROOT / 'neurobridge', root / 'neurobridge', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'windows', root / 'windows', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'web', root / 'web')
            host = root / 'windows/project_service.py'
            host.write_text(host.read_text(encoding='utf-8').replace("SERVICE_NAME = 'NeuroBridgeProject'", f"SERVICE_NAME = '{name}'"), encoding='utf-8')
            create_config(root)
            shutil.copy2(bridge, root / '.runtime/algorithm/neurobridge_affective_bridge.exe')
            subprocess.run([sys.executable, '-m', 'venv',
                            str(root / '.runtime/windows-venv')], check=True, timeout=60)
            venv_python = root / '.runtime/windows-venv/Scripts/python.exe'
            subprocess.run([str(venv_python), '-m', 'pip', 'install', '--disable-pip-version-check',
                            '-r', str(ROOT / 'requirements.lock')], check=True, timeout=180)
            python = Path(sys._base_executable)
            url = subprocess.check_output([str(venv_python), str(root / 'windows/gateway_helper.py'), 'url'],
                                          text=True, timeout=10).strip()
            self.assertEqual(url, 'http://127.0.0.1:8080/capture/')
            processes = []

            def remember_process(handle):
                pid = ws.QueryServiceStatusEx(handle)['ProcessId']
                if pid:
                    processes.append(win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid))
                return pid

            def wait_for_processes():
                outcomes = []
                while processes:
                    process = processes.pop()
                    try:
                        outcomes.append(win32event.WaitForSingleObject(process, 30000))
                    finally:
                        win32api.CloseHandle(process)
                self.assertTrue(all(result == win32event.WAIT_OBJECT_0 for result in outcomes),
                                'Stopped service host must exit and release logs before restart/fixture cleanup')

            with patch.object(service, 'SERVICE_NAME', name):
                try:
                    service.enable(root, python)
                    first = service.status(root, python)
                    self.assertEqual(first['state'], ws.SERVICE_RUNNING)
                    self.assertTrue(first['automatic'])
                    with service.service_handle(root, python) as (_, handle):
                        first_pid = remember_process(handle)
                    with urlopen(url, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                    service.enable(root, python)
                    self.assertEqual(service.status(root, python)['state'], ws.SERVICE_RUNNING)
                    with service.service_handle(root, python) as (_, handle):
                        self.assertEqual(ws.QueryServiceStatusEx(handle)['ProcessId'], first_pid)
                    # Exercise PowerShell action dispatch as well as the SCM API.
                    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(root / 'windows/setup-windows-gateway.ps1'), '-Action', 'autostart-disable'],
                        capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    final = service.status(root, python)
                    self.assertFalse(final['automatic'])
                    self.assertFalse(final['configuredAutostart'])
                    self.assertEqual(final['state'], ws.SERVICE_STOPPED)
                    wait_for_processes()
                    service.enable(root, python)
                    with service.service_handle(root, python) as (_, handle):
                        remember_process(handle)
                    self.assertTrue(service.status(root, python)['automatic'])
                except BaseException:
                    # Temporary service logs would otherwise disappear before CI uploads artifacts.
                    for log in (root / '.runtime/logs').glob('*.log'):
                        print(f'--- {log.name} ---\n{log.read_text(encoding="utf-8", errors="replace")[-16000:]}')
                    raise
                finally:
                    try:
                        with service.service_handle(root, python, write=True) as (_, handle):
                            if handle:
                                if ws.QueryServiceStatus(handle)[1] != ws.SERVICE_STOPPED:
                                    remember_process(handle)
                                    ws.ControlService(handle, ws.SERVICE_CONTROL_STOP)
                                    service.wait_state(handle, ws.SERVICE_STOPPED)
                                ws.DeleteService(handle)
                    finally:
                        # SERVICE_STOPPED can precede interpreter/DLL shutdown.
                        # Keep kernel process handles across that transition.
                        wait_for_processes()
