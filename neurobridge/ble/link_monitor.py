"""Explicit operator HCI monitoring; persist only disconnect metadata, never ACL data."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import re


class DisconnectFilter:
    def __init__(self) -> None:
        self.selected = False

    def feed(self, line: str) -> str | None:
        # Any new packet ends the previous event, including ACL payload packets.
        if re.match(r"^[<>@=*#]", line):
            self.selected = bool(re.search(r"HCI (?:Event: Disconnect Complete|Command: Disconnect)\b", line))
            return line.strip() if self.selected else None
        if self.selected and re.match(r"^\s+(?:Status|Handle|Reason):", line):
            return line.strip()
        return None


async def monitor(output: Path, index: str, duration: float) -> None:
    # Refuse symlinks; opening the log must never follow an arbitrary root path.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "a", encoding="utf-8", buffering=1) as log:
        def write(value: str) -> None:
            log.write(f"{datetime.now(timezone.utc).isoformat()} {value}\n")
        write(f"HCI disconnect monitor started: index={index} durationSeconds={duration}")
        process = await asyncio.create_subprocess_exec(
            "btmon", "--index", index, "--date", "--no-pager",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        parser = DisconnectFilter()
        try:
            async with asyncio.timeout(duration):
                while line := await process.stdout.readline():
                    filtered = parser.feed(line.decode("utf-8", errors="replace"))
                    if filtered:
                        write(filtered)
                await process.wait()
                if process.returncode:
                    raise RuntimeError(f"btmon exited with code {process.returncode}; check root permissions and controller index")
        except TimeoutError:
            pass
        finally:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            write(f"HCI disconnect monitor stopped: returnCode={process.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/var/log/neurobridge/ble-link.log"))
    parser.add_argument("--index", default="hci0")
    parser.add_argument("--duration-seconds", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 86400 or not re.fullmatch(r"(?:hci)?[0-9]+", args.index):
        parser.error("duration must be 1..86400 seconds and index must be a controller number or hciN")
    asyncio.run(monitor(args.output, args.index, args.duration_seconds))


if __name__ == "__main__":
    main()
