"""Deployment capabilities used to gate optional application behavior."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileCapabilities:
    supports_recording: bool
    supports_replay: bool
    supports_local_ui: bool
    supports_wired_b_side: bool
    max_device_count: int
    allowed_streams: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_streams", frozenset(self.allowed_streams))
        if self.max_device_count != 1:
            raise ValueError("Current NeuroBridge profiles support exactly one device")
        if self.supports_local_ui == self.supports_wired_b_side:
            raise ValueError("A profile must select exactly one northbound access strategy")
