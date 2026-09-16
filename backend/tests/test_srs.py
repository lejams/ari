"""SM-2 schedule: pure, deterministic, versioned."""

from datetime import UTC, datetime, timedelta

from ari.domain.models import SrsRating, SrsState
from ari.domain.srs import SRS_VERSION, schedule

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def test_good_reviews_grow_the_interval_one_three_then_by_ease() -> None:
    state = SrsState(due_at=NOW)
    first = schedule(state, SrsRating.GOOD, NOW)
    second = schedule(first, SrsRating.GOOD, first.due_at)
    third = schedule(second, SrsRating.GOOD, second.due_at)

    assert (first.interval_days, second.interval_days) == (1, 3)
    assert third.interval_days == round(3 * 2.5)
    assert third.repetitions == 3
    assert third.due_at == second.due_at + timedelta(days=third.interval_days)
    assert third.lapses == 0


def test_again_resets_the_interval_counts_a_lapse_and_lowers_the_ease() -> None:
    state = SrsState(due_at=NOW, interval_days=8, ease=2.5, repetitions=3)
    result = schedule(state, SrsRating.AGAIN, NOW)

    assert result.due_at == NOW
    assert result.interval_days == 0
    assert result.repetitions == 0
    assert result.lapses == 1
    assert result.ease == 2.3
    # The ease never drops below the floor.
    floor = schedule(SrsState(due_at=NOW, ease=1.3), SrsRating.AGAIN, NOW)
    assert floor.ease == 1.3


def test_hard_and_easy_bracket_the_good_interval() -> None:
    state = SrsState(due_at=NOW, interval_days=10, ease=2.5, repetitions=4)
    hard = schedule(state, SrsRating.HARD, NOW)
    good = schedule(state, SrsRating.GOOD, NOW)
    easy = schedule(state, SrsRating.EASY, NOW)

    assert hard.interval_days < good.interval_days < easy.interval_days
    assert hard.ease < state.ease < easy.ease
    assert SRS_VERSION == "srs-sm2-v1"
