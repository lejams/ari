"""Spaced repetition schedule, SM-2 simplified. Pure function, versioned like a rubric."""

from datetime import datetime, timedelta

from ari.domain.models import SrsRating, SrsState

SRS_VERSION = "srs-sm2-v1"
MIN_EASE = 1.3
FIRST_INTERVAL_DAYS = 1
SECOND_INTERVAL_DAYS = 3


def schedule(state: SrsState, rating: SrsRating, now: datetime) -> SrsState:
    """Next schedule after one review.

    - again: the word is due immediately, a lapse is counted and the ease drops;
    - hard: the interval grows slowly, the ease drops a little;
    - good: the interval grows by the ease factor (1 day, then 3 days, then interval x ease);
    - easy: the interval grows faster and the ease increases.
    """
    ease = state.ease
    if rating is SrsRating.AGAIN:
        return SrsState(
            due_at=now,
            interval_days=0,
            ease=max(MIN_EASE, ease - 0.2),
            repetitions=0,
            lapses=state.lapses + 1,
            last_reviewed_at=now,
        )
    if rating is SrsRating.HARD:
        ease = max(MIN_EASE, ease - 0.15)
        interval = max(FIRST_INTERVAL_DAYS, round(state.interval_days * 1.2))
    elif state.repetitions == 0:
        interval = FIRST_INTERVAL_DAYS
    elif state.repetitions == 1:
        interval = SECOND_INTERVAL_DAYS
    else:
        interval = max(state.interval_days + 1, round(state.interval_days * ease))
    if rating is SrsRating.EASY:
        ease = ease + 0.15
        interval = max(interval + 1, round(interval * 1.3))
    return SrsState(
        due_at=now + timedelta(days=interval),
        interval_days=interval,
        ease=round(ease, 4),
        repetitions=state.repetitions + 1,
        lapses=state.lapses,
        last_reviewed_at=now,
    )
