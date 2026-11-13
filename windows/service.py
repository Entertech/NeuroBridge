#!/usr/bin/env python3
"""Windows Service host for the normal NeuroBridge bootstrap entrypoint."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import NamedTemporaryFile
import threading
import tomllib
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.__main__ import run


DEFAULT_CONFIG = Path(r"C:\ProgramData\NeuroBridge\gateway.toml")
CONFIG_TEMPLATE = Path(__file__).with_name("gateway.toml.example")
DATA_DIRECTORIES = ("logs", "recordings")
# The published v0.3.2 Windows template, before it supported the headband.
LEGACY_DEFAULT_SHA256 = "a9ee184f7325b0fb2e18dc862bad159687986bee2cbb474509a7374143c6a7cc"
LEGACY_DEFAULT_VALUES_SHA256 = "dec49bd8d974a0c051984b74542eb3f7ef277f99c8bdeb8f3712cea76eb9bd36"


def provision_data_layout(config_path: Path = DEFAULT_CONFIG, template: Path = CONFIG_TEMPLATE) -> bool:
    """Create the ProgramData layout this service host needs before it can run.

    The MSI registers the service but owns nothing under ProgramData, while this
    host reads a fixed configuration path.  Create the directories the shipped
    template points at, then copy the template once if configuration is missing.

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


def upgrade_legacy_default(config_path: Path, template: Path) -> Path | None:
    """Upgrade the v0.3.2 factory settings and keep a backup of the old file."""
    original = config_path.read_bytes()
    if sha256(original.replace(b"\r\n", b"\n")).hexdigest() != LEGACY_DEFAULT_SHA256:
        # Schema migration can remove comments. Accept that form only if all
        # values match, and leave files containing operator comments alone.
        if b"#" in original:
            return None
        try:
            values = tomllib.loads(original.decode("utf-8"))
            serialized = json.dumps(values, sort_keys=True).encode("utf-8")
        except (ValueError, TypeError):
            return None
        if sha256(serialized).hexdigest() != LEGACY_DEFAULT_VALUES_SHA256:
            return None

    updated = template.read_bytes()
    try:
        data_source = tomllib.loads(updated.decode("utf-8")).get("data_source", {})
    except ValueError as error:
        raise OSError("The shipped Windows configuration template is invalid") from error
    if not isinstance(data_source, dict) or data_source.get("include_headband") is not True:
        raise OSError("The shipped Windows template is missing the dual-source default")

    backup_directory = config_path.parent / "config-backups"
    backup_directory.mkdir(exist_ok=True)
    backup = backup_directory / f"{config_path.name}.before-dual-source.{uuid4().hex}.bak"
    with backup.open("xb") as backup_file:
        backup_file.write(original)
        backup_file.flush()
        os.fsync(backup_file.fileno())
    temporary = None
    try:
        with NamedTemporaryFile(dir=config_path.parent, prefix=".gateway-", suffix=".tmp", delete=False) as staged:
            temporary = Path(staged.name)
            staged.write(updated)
            staged.flush()
            os.fsync(staged.fileno())
        if config_path.read_bytes() != original:
            return None
        os.replace(temporary, config_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


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
            if not created:
                try:
                    backup = upgrade_legacy_default(DEFAULT_CONFIG, CONFIG_TEMPLATE)
                    if backup is not None:
                        servicemanager.LogInfoMsg(f"Upgraded the old Windows defaults to dual-source capture; backup: {backup}")
                except OSError as error:
                        servicemanager.LogErrorMsg(f"Windows default configuration upgrade failed: {error}")
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
