"""Pure, bounded 16-bit sequence accounting shared by validation and parsing."""

from collections import deque
from dataclasses import dataclass

SEQUENCE_MODULUS = 1 << 16
SEQUENCE_HALF_RANGE = 1 << 15
RECENT_SEQUENCE_WINDOW = 4096


@dataclass(frozen=True)
class LossSnapshot:
    base_sequence: int | None
    highest_sequence: int | None
    expected_packets: int
    received_unique_packets: int
    lost_packets: int
    loss_rate_percent: float
    duplicate_packets: int
    out_of_order_packets: int
    late_packets: int


@dataclass(frozen=True)
class SequenceObservation:
    classification: str
    sequence: int
    expected_sequence: int | None
    gap_packets: int
    snapshot: LossSnapshot


class SequenceLossTracker:
    """RFC 3550-style cumulative loss using extended 16-bit sequence numbers.

    Expected packets are ``extended_highest - base + 1``. Received packets count
    unique packets only, so duplicates never improve the loss rate. Reordered
    packets within a bounded window can fill an earlier gap and reduce loss.
    """

    def __init__(self, recent_window: int = RECENT_SEQUENCE_WINDOW) -> None:
        self.recent_window = recent_window
        self.base_extended: int | None = None
        self.highest_extended: int | None = None
        self.received_unique = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.late = 0
        self._recent_order: deque[int] = deque()
        self._recent_seen: set[int] = set()

    def _extend(self, sequence: int) -> int:
        assert self.highest_extended is not None
        cycle = self.highest_extended & ~(SEQUENCE_MODULUS - 1)
        candidate = cycle | sequence
        delta = candidate - self.highest_extended
        if delta < -SEQUENCE_HALF_RANGE:
            candidate += SEQUENCE_MODULUS
        elif delta > SEQUENCE_HALF_RANGE:
            candidate -= SEQUENCE_MODULUS
        return candidate

    def _remember(self, extended: int) -> None:
        self._recent_seen.add(extended)
        self._recent_order.append(extended)
        while len(self._recent_order) > self.recent_window:
            expired = self._recent_order.popleft()
            self._recent_seen.discard(expired)

    def observe(self, sequence: int) -> SequenceObservation:
        if not 0 <= sequence < SEQUENCE_MODULUS:
            raise ValueError("sequence must be an unsigned 16-bit value")
        if self.highest_extended is None:
            self.base_extended = sequence
            self.highest_extended = sequence
            self.received_unique = 1
            self._remember(sequence)
            return SequenceObservation("baseline", sequence, None, 0, self.snapshot())

        expected_sequence = (self.highest_extended + 1) % SEQUENCE_MODULUS
        extended = self._extend(sequence)
        gap_packets = 0
        if extended > self.highest_extended:
            gap_packets = extended - self.highest_extended - 1
            classification = "gap" if gap_packets else "in_order"
            self.highest_extended = extended
            self.received_unique += 1
            self._remember(extended)
        elif extended in self._recent_seen:
            classification = "duplicate"
            self.duplicates += 1
        elif extended >= max(self.base_extended or 0, self.highest_extended - self.recent_window + 1):
            classification = "out_of_order"
            self.out_of_order += 1
            self.received_unique += 1
            self._remember(extended)
        else:
            classification = "late"
            self.late += 1
        return SequenceObservation(
            classification,
            sequence,
            expected_sequence,
            gap_packets,
            self.snapshot(),
        )

    def snapshot(self) -> LossSnapshot:
        if self.base_extended is None or self.highest_extended is None:
            return LossSnapshot(None, None, 0, 0, 0, 0.0, self.duplicates, self.out_of_order, self.late)
        expected = self.highest_extended - self.base_extended + 1
        lost = max(0, expected - self.received_unique)
        return LossSnapshot(
            self.base_extended % SEQUENCE_MODULUS,
            self.highest_extended % SEQUENCE_MODULUS,
            expected,
            self.received_unique,
            lost,
            lost * 100.0 / expected if expected else 0.0,
            self.duplicates,
            self.out_of_order,
            self.late,
        )


