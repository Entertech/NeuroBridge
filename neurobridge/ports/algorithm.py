from __future__ import annotations

from typing import Protocol

from ..domain.algorithm import AlgorithmInput, AlgorithmResult, AlgorithmSession, AlgorithmState


class AlgorithmEngine(Protocol):
    async def initialize(self, session: AlgorithmSession) -> AlgorithmState: ...
    async def evaluate(self, value: AlgorithmInput) -> AlgorithmResult: ...
    async def close(self) -> None: ...
