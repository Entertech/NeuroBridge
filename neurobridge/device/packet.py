"""Transport-neutral packet emitted at the device adapter boundary."""

from __future__ import annotations

from dataclasses import dataclass
import time


def wall_clock_ms() -> int:
    """Return the wall-clock receive time used by recordings and windows."""

    return int(time.time() * 1000)


@dataclass(frozen=True)
class DevicePacket:
    """One unmodified device-side payload with its adapter receive context."""

    transport: str
    channel: str
    value: bytes
    received_at_ms: int

    @classmethod
    def received(cls, transport: str, channel: str, value: bytes) -> "DevicePacket":
        """Capture time at the adapter callback/read boundary."""

        return cls(transport, channel, bytes(value), wall_clock_ms())
