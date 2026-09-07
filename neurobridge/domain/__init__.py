"""Transport-independent NeuroBridge domain models."""

from .algorithm import AlgorithmInput, AlgorithmResult, AlgorithmSession, AlgorithmState
from .capabilities import ProfileCapabilities
from .raw import DeviceFrame, ParseDiagnostic, ParseOutcome, RawChunk
from .result import WindowResult
from .signal import ParsedSignal, ParsedSignalBatch
from .status import (
    ConnectionState,
    DataState,
    DataStatusSnapshot,
    DeviceConnectionEvent,
    StorageState,
)

__all__ = [
    "AlgorithmInput",
    "AlgorithmResult",
    "AlgorithmSession",
    "AlgorithmState",
    "ConnectionState",
    "DataState",
    "DataStatusSnapshot",
    "DeviceConnectionEvent",
    "DeviceFrame",
    "ParseDiagnostic",
    "ParseOutcome",
    "ParsedSignal",
    "ParsedSignalBatch",
    "ProfileCapabilities",
    "RawChunk",
    "StorageState",
    "WindowResult",
]
