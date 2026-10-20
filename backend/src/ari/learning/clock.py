"""Time and randomness are injected, never read.

The core never reads the system clock and never touches a global random generator: every
function receives `now` explicitly and anything random receives a `RandomSource`. That is what
makes a replay deterministic and a simulation reproducible. The application supplies the real
clock (`lambda: utc_now()`) and a seeded generator; tests supply a fixed instant.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

Clock = Callable[[], datetime]


class RandomSource(Protocol):
    """What a policy needs from a random generator; a seeded Python generator satisfies it."""

    def random(self) -> float:
        """A float in [0, 1)."""
        ...
