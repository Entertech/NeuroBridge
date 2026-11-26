"""Estimate the time of each EEG sample from one source's own packet clock."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median

from ..domain.signal import ParsedSignalBatch


# Both inputs provide three samples per channel in each SDK EEG packet.
SAMPLES_PER_PACKET = 3


@dataclass(frozen=True, slots=True)
class EegSampleTime:
    step_ms: float
    origin_ms: float

    def at(self, index: int) -> float:
        return self.origin_ms + index * self.step_ms


class EegPacketClock:
    def __init__(self, window_ms: int, max_packets: int) -> None:
        self._window_ms = window_ms
        self._observations: deque[tuple[int, int]] = deque(maxlen=max_packets)
        self._sequence: int | None = None
        self._position = 0

    def observe(self, batch: ParsedSignalBatch) -> tuple[int, ...]:
        packet_positions = []
        ordered = True
        for signal in batch.signals:
            if signal.signal_type != "eeg":
                continue
            payload = signal.samples
            if not signal.valid or not isinstance(payload, bytes):
                ordered = False
                continue
            if batch.device_protocol == "headband_ble":
                sequence = int.from_bytes(payload[:2], "big") if len(payload) == 20 else None
            else:
                sequence = signal.window_hint.get("sequence") if len(payload) == 18 else None
            if type(sequence) is not int or not 0 <= sequence < 65536:
                ordered = False
                continue
            delta = (sequence - self._sequence) % 65536 if self._sequence is not None else 0
            timestamp = signal.received_at_ms
            if self._sequence is not None and (not 0 < delta < 32768 or timestamp < self._observations[-1][1]):
                ordered = False
                continue
            self._sequence = sequence
            self._position += delta
            self._observations.append((self._position, timestamp))
            packet_positions.append(self._position)
            while self._observations[0][1] < timestamp - self._window_ms * 12:
                self._observations.popleft()
        if not ordered:
            return ()
        return tuple(packet * SAMPLES_PER_PACKET + sample
                     for packet in packet_positions for sample in range(SAMPLES_PER_PACKET))

    def estimate(self) -> EegSampleTime | None:
        observations = tuple(self._observations)
        if len(observations) < 4 or observations[-1][1] - observations[0][1] < 3 * self._window_ms:
            return None
        # Use widely separated packets for rate estimation. The lower envelope
        # discounts delivery bursts, which can delay but cannot advance arrivals.
        lag = len(observations) // 2
        packet_ms = median((end_ms - start_ms) / (end_position - start_position)
                           for (start_position, start_ms), (end_position, end_ms)
                           in zip(observations, observations[lag:]))
        if packet_ms <= 0:
            return None
        origin = min(timestamp - position * packet_ms for position, timestamp in observations)
        return EegSampleTime(packet_ms / SAMPLES_PER_PACKET, origin - 2 * packet_ms / SAMPLES_PER_PACKET)
