"""M3 Windows COM source extension point.

Windows is intentionally not advertised as delivered until its target-platform
contract and service tests exist.
"""

from __future__ import annotations


class WindowsSerialSource:
    async def start(self) -> None:
        raise NotImplementedError("Windows COM support belongs to the M3 delivery stage")

    async def stop(self) -> None:
        return None
