#!/usr/bin/env python3
"""Windows Service host for the normal NeuroBridge bootstrap entrypoint."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.__main__ import run


DEFAULT_CONFIG = Path(r"C:\ProgramData\NeuroBridge\gateway.toml")
CONFIG_TEMPLATE = ROOT / "gateway.toml.example"
DATA_DIRECTORIES = ("logs", "recordings")


def provision_data_layout(config_path: Path = DEFAULT_CONFIG, template: Path = CONFIG_TEMPLATE) -> bool:
    """Create the ProgramData layout this service host needs before it can run.

    The MSI registers the service but owns nothing under ProgramData, while this
    host reads a fixed configuration path.  Create the directories the shipped
    template points at, then copy the template once.  An existing configuration
    is never replaced, so operator settings and recorded data survive both a
    reinstall and an uninstall.

    Returns True only when a new configuration file was written.
    """
    data_root = config_path.parent
    for name in DATA_DIRECTORIES:
        (data_root / name).mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        return False
    if not template.is_file():
        raise FileNotFoundError(f"Shipped configuration template is missing: {template}")
    data_root.mkdir(parents=True, exist_ok=True)
    # Exclusive create keeps a concurrently provisioned configuration intact.
    with template.open("rb") as source, config_path.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    return True


def _service_main() -> None:
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil
    except ImportError as error:
        raise RuntimeError("pywin32 is required to host NeuroBridge as a Windows Service") from error

    class NeuroBridgeService(win32serviceutil.ServiceFramework):
        _svc_name_ = "NeuroBridge"
        _svc_display_name_ = "NeuroBridge Gateway"
        _svc_description_ = "USB COM headset to loopback WebSocket gateway"

        def __init__(self, args) -> None:
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.loop: asyncio.AbstractEventLoop | None = None
            self.task: asyncio.Task[None] | None = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self._cancel)

        def _cancel(self) -> None:
            if self.task is not None:
                self.task.cancel()

        def SvcDoRun(self) -> None:
            servicemanager.LogInfoMsg("NeuroBridge service starting")
            try:
                created = provision_data_layout()
            except Exception as error:
                # Without the data layout the gateway cannot load its
                # configuration, so fail loudly in the event log instead of
                # exiting with a bare traceback the operator never sees.
                servicemanager.LogErrorMsg(f"NeuroBridge data layout provisioning failed: {error}")
                raise
            servicemanager.LogInfoMsg(
                "NeuroBridge data layout ready"
                + (" (created the initial configuration)" if created else " (existing configuration preserved)")
            )
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.task = self.loop.create_task(run(str(DEFAULT_CONFIG)))
            try:
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass
            finally:
                self.loop.close()
                servicemanager.LogInfoMsg("NeuroBridge service stopped")

    if len(sys.argv) <= 1:
        # The SCM starts the host with no additional arguments.  HandleCommandLine
        # treats that as a usage error, prints its help text and exits before the
        # dispatcher runs, so the service would never report SERVICE_RUNNING and
        # the MSI would fail with error 1920.  Dispatch directly instead, the way
        # the project service host already does.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(NeuroBridgeService)
        servicemanager.StartServiceCtrlDispatcher()
        return
    win32serviceutil.HandleCommandLine(NeuroBridgeService)


if __name__ == "__main__":
    _service_main()
