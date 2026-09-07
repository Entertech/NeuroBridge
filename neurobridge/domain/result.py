"""Atomic application result values."""

from __future__ import annotations

from dataclasses import dataclass

from .algorithm import AlgorithmResult
from .signal import ParsedSignalBatch


@dataclass(frozen=True, slots=True)
class WindowResult:
    batch: ParsedSignalBatch
    algorithm_result: AlgorithmResult
    mode: str
    completed_at_ms: int
    persistence_guaranteed: bool

    def __post_init__(self) -> None:
        if self.algorithm_result.batch_id != self.batch.batch_id:
            raise ValueError("WindowResult batch identifiers do not match")
        if self.mode not in {"live", "replay"}:
            raise ValueError("WindowResult mode must be live or replay")

    @property
    def valid(self) -> bool:
        return self.batch.valid and self.algorithm_result.valid
