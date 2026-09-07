#!/usr/bin/env python3
"""Windows Service host for the normal NeuroBridge bootstrap entrypoint."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.__main__ import run


DEFAULT_CONFIG = Path(r"C:\ProgramData\NeuroBridge\gateway.toml")


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

    win32serviceutil.HandleCommandLine(NeuroBridgeService)


if __name__ == "__main__":
    _service_main()
