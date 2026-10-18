from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MonthCost:
    """What a learner's recorded model calls cost. Unknown costs are counted, never summed."""

    known_micro_eur: int
    unknown_calls: int


class UsageLedger(Protocol):
    """Read side of the recorded usage. The window is half-open: `start <= t < end`."""

    def exam_sessions_started(self, learner_id: str, start: datetime, end: datetime) -> int: ...

    def cost(self, learner_id: str, start: datetime, end: datetime) -> MonthCost: ...
