"""SCM host and management for the project-local Windows gateway."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if sys.platform == 'win32' and sys.argv[1:] == ['host']:
    # SCM must launch the real interpreter: Windows venv python.exe is a
    # redirector that creates a child process. The -I -S host loads only this
    # project's dependency directory, including pywin32's DLL bootstrap .pth.
    import site
    environment = ROOT / '.runtime/windows-venv'
    sys.prefix = sys.exec_prefix = str(environment)
    site.addsitedir(str(environment / 'Lib/site-packages'))
sys.path.insert(0, str(ROOT))
from windows.gateway_helper import config_path, validate_config, check_algorithm, check_ports, instance_lock

SERVICE_NAME = 'NeuroBridgeProject'
DESCRIPTION = 'NeuroBridge project-local USB COM gateway (managed by project_service.py)'


def preference_path(root: Path) -> Path:
    return root / '.runtime/config/windows-autostart.json'


def autostart_enabled(root: Path) -> bool:
    path = preference_path(root)
    if not path.exists():
        return True
    value = json.loads(path.read_text(encoding='utf-8'))
    if type(value.get('enabled')) is not bool:
        raise ValueError('windows-autostart.json requires a boolean enabled value')
    return value['enabled']


def save_preference(root: Path, enabled: bool) -> None:
    path = preference_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'enabled': enabled}) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def service_command(root: Path, python: Path) -> str:
    # Always quote the executable, even without spaces (SCM unquoted path protection).
    return f'"{python}" -I -S -u "{root / "windows/project_service.py"}" host'


def verify_owner(configuration, root: Path, python: Path) -> None:
    if configuration[3].casefold() != service_command(root, python).casefold():
        raise ValueError(f'{SERVICE_NAME} belongs to another installation; refusing to overwrite or stop it')
    if configuration[7].casefold() != 'localsystem':
        raise ValueError(f'{SERVICE_NAME} has a different service account; refusing to change it')


@contextmanager
def service_handle(root: Path, python: Path, *, write: bool = False):
    import win32service as ws
    import pywintypes
    manager = ws.OpenSCManager(None, None, ws.SC_MANAGER_ALL_ACCESS if write else ws.SC_MANAGER_CONNECT)
    service = None
    try:
        try:
            service = ws.OpenService(manager, SERVICE_NAME, ws.SERVICE_ALL_ACCESS if write else ws.SERVICE_QUERY_CONFIG | ws.SERVICE_QUERY_STATUS)
        except pywintypes.error as error:
            if error.winerror != 1060:
                raise
        if service is not None:
            verify_owner(ws.QueryServiceConfig(service), root, python)
        yield manager, service
    finally:
        if service is not None:
            ws.CloseServiceHandle(service)
        ws.CloseServiceHandle(manager)


def status(root: Path, python: Path) -> dict:
    import win32service as ws
    with service_handle(root, python) as (_, service):
        return {'installed': service is not None,
                'state': ws.QueryServiceStatus(service)[1] if service is not None else ws.SERVICE_STOPPED,
                'automatic': ws.QueryServiceConfig(service)[1] == ws.SERVICE_AUTO_START if service is not None else False,
                'configuredAutostart': autostart_enabled(root)}


def wait_state(service, wanted: int, timeout: float = 45) -> None:
    import win32service as ws
    deadline = time.monotonic() + timeout
    while True:
        state = ws.QueryServiceStatus(service)[1]
        if state == wanted:
            return
        if wanted == ws.SERVICE_RUNNING and state == ws.SERVICE_STOPPED:
            raise RuntimeError('Service stopped during startup; check .runtime/logs/windows-service.log and Windows Event Viewer')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Service did not reach state {wanted}; check .runtime/logs/windows-service.log and Windows Event Viewer')
        time.sleep(0.25)


def enable(root: Path, python: Path) -> None:
    import win32service as ws
    config = validate_config(config_path(root))
    check_algorithm(config)
    with service_handle(root, python, write=True) as (manager, service):
        if service is None:
            # Detect a foreground process before installing; never kill it implicitly.
            with instance_lock(root):
                check_ports(config)
            service = ws.CreateService(manager, SERVICE_NAME, 'NeuroBridge Project Gateway',
                                       ws.SERVICE_ALL_ACCESS, ws.SERVICE_WIN32_OWN_PROCESS,
                                       ws.SERVICE_AUTO_START, ws.SERVICE_ERROR_NORMAL,
                                       service_command(root, python), None, 0, None, None, None)
            created = True
        else:
            created = False
        try:
            ws.ChangeServiceConfig(service, ws.SERVICE_NO_CHANGE, ws.SERVICE_AUTO_START,
                                   ws.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None)
            ws.ChangeServiceConfig2(service, ws.SERVICE_CONFIG_DESCRIPTION, DESCRIPTION)
            ws.ChangeServiceConfig2(service, ws.SERVICE_CONFIG_FAILURE_ACTIONS,
                                   {'ResetPeriod': 86400, 'RebootMsg': '', 'Command': '',
                                    'Actions': [(ws.SC_ACTION_RESTART, 3000)] * 3})
            save_preference(root, True)
            state = ws.QueryServiceStatus(service)[1]
            if state == ws.SERVICE_STOP_PENDING:
                wait_state(service, ws.SERVICE_STOPPED)
                state = ws.SERVICE_STOPPED
            if state == ws.SERVICE_STOPPED:
                ws.StartService(service, None)
            wait_state(service, ws.SERVICE_RUNNING)
        finally:
            if created:
                ws.CloseServiceHandle(service)


def disable(root: Path, python: Path) -> None:
    import win32service as ws
    with service_handle(root, python, write=True) as (_, service):
        if service is not None:
            ws.ChangeServiceConfig(service, ws.SERVICE_NO_CHANGE, ws.SERVICE_DEMAND_START,
                                   ws.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None)
            state = ws.QueryServiceStatus(service)[1]
            if state != ws.SERVICE_STOPPED:
                if state == ws.SERVICE_START_PENDING:
                    wait_state(service, ws.SERVICE_RUNNING)
                    state = ws.SERVICE_RUNNING
                if state != ws.SERVICE_STOP_PENDING:
                    ws.ControlService(service, ws.SERVICE_CONTROL_STOP)
                wait_state(service, ws.SERVICE_STOPPED)
        save_preference(root, False)


async def serve_until_stopped(config, stopped: threading.Event, running, *, gateway_run=None, startup_timeout=30):
    if gateway_run is None:
        from neurobridge.__main__ import run as gateway_run
    task = asyncio.create_task(gateway_run(str(config_path(ROOT))))
    ready = False
    deadline = time.monotonic() + startup_timeout
    try:
        while not task.done():
            if stopped.is_set():
                return
            if not ready:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Gateway UI listener did not become ready')
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', config.local_ui.port), 0.5)
                    writer.close()
                    await writer.wait_closed()
                    if stopped.is_set() or task.done():
                        continue
                    running()
                    ready = True
                except OSError:
                    pass
                except TimeoutError:
                    pass
            await asyncio.sleep(0.2)
        await task  # Propagate startup/runtime failures to the service host.
        if not stopped.is_set():
            raise RuntimeError('Gateway exited unexpectedly')
    finally:
        task.cancel()
        try:
            await asyncio.wait_for(task, config.pipeline.shutdown_timeout_ms / 1000 + 5)
        except asyncio.CancelledError:
            pass


def host() -> None:
    import servicemanager
    import win32service as ws
    import win32serviceutil

    class ProjectService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = 'NeuroBridge Project Gateway'

        def __init__(self, args):
            super().__init__(args)
            self.stopped = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(ws.SERVICE_STOP_PENDING, waitHint=30000)
            self.stopped.set()

        def SvcShutdown(self):
            self.SvcStop()

        def SvcRun(self):
            # Report RUNNING only when the gateway has actually opened its UI listener.
            self.ReportServiceStatus(ws.SERVICE_START_PENDING, waitHint=45000)
            os.chdir(ROOT)
            logger = logging.getLogger('neurobridge.windows_service_host')
            logger.setLevel(logging.INFO)
            logger.propagate = False
            try:
                directory = ROOT / '.runtime/logs'
                directory.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(directory / 'windows-service.log', maxBytes=1048576, backupCount=3, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
                logger.addHandler(handler)
                logger.info('Service starting; project=%s', ROOT)
                config = validate_config(config_path(ROOT))
                with instance_lock(ROOT):
                    check_ports(config)
                    check_algorithm(config)
                    asyncio.run(serve_until_stopped(config, self.stopped,
                                lambda: self.ReportServiceStatus(ws.SERVICE_RUNNING)))
                logger.info('Service stopped cleanly')
                self.ReportServiceStatus(ws.SERVICE_STOPPED)
            except BaseException:
                logger.exception('Service failed')
                servicemanager.LogErrorMsg('NeuroBridge project service failed; inspect .runtime/logs/windows-service.log')
                # Abnormal process exit lets SCM apply configured recovery actions.
                logging.shutdown()
                os._exit(1)

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(ProjectService)
    servicemanager.StartServiceCtrlDispatcher()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['host', 'enable', 'disable', 'status', 'preference', 'state'])
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows service control requires Windows')
    python = Path(sys._base_executable)
    control_log = logging.getLogger('neurobridge.windows_service_control')
    try:
        if args.action in {'enable', 'disable'}:
            directory = ROOT / '.runtime/logs'
            directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(directory / 'windows-service-control.log', maxBytes=1048576, backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            control_log.addHandler(handler)
            control_log.setLevel(logging.INFO)
            control_log.info('Service action=%s project=%s', args.action, ROOT)
        if args.action == 'host':
            host()
        elif args.action == 'preference':
            print('enabled' if autostart_enabled(ROOT) else 'disabled')
        elif args.action == 'state':
            print(status(ROOT, python)['state'])
        else:
            if args.action == 'enable':
                enable(ROOT, python)
            elif args.action == 'disable':
                disable(ROOT, python)
            print(json.dumps(status(ROOT, python)))
            control_log.info('Service action completed: %s', args.action)
    except Exception as error:
        if control_log.handlers:
            control_log.exception('Service action failed: %s', args.action)
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
