"""mastery-beta-v1: what each rule does, then the properties that must hold for any history."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta

import pytest
from toy_domain import T0, obs

from ari.learning import BetaMasteryModel, MasteryParams, Observation, Skill

FRACTIONS = Skill("fractions", "Fractions", "number", half_life_days=45.0)
EXAM_RULES = Skill("exam_rules", "Exam rules", "meta", half_life_days=30.0, critical=True)
MODEL = BetaMasteryModel()


def estimate(skill: Skill, observations: list[Observation], days: float = 0.0, **kw):  # type: ignore[no-untyped-def]
    model = BetaMasteryModel(kw.pop("params", None))
    return model.estimate(skill, observations, T0 + timedelta(days=days), **kw)


def successes(count: int, *, attempts: int = 3, skill: str = "fractions", **kw):  # type: ignore[no-untyped-def]
    """`count` successes on distinct items, spread over `attempts` attempts."""
    return [
        obs(skill, item=f"i{n}", attempt=f"a{n % attempts}", id=f"s{n:03d}", **kw)
        for n in range(count)
    ]


# ----- no evidence, little evidence -------------------------------------------------------


def test_without_evidence_a_skill_is_unknown_with_the_prior_interval() -> None:
    result = estimate(FRACTIONS, [])
    assert result.status == "unknown" and result.last_observed_at is None
    assert (result.mean, result.effective_n, result.distinct_items) == (0.5, 0.0, 0)
    assert result.lower == pytest.approx(0.1) and result.upper == pytest.approx(0.9)
    assert result.model_version == "mastery-beta-v1"


def test_a_prior_can_be_supplied_and_fades_into_the_skill() -> None:
    informed = estimate(FRACTIONS, [], prior=(6.0, 2.0))
    assert informed.mean == pytest.approx(0.75) and informed.status == "unknown"


def test_a_failing_skill_is_uncertain_at_first_and_weak_only_with_enough_evidence() -> None:
    def after(failures: int) -> str:
        return estimate(FRACTIONS, successes(failures, attempts=failures, kind="failure")).status

    assert after(1) == "uncertain" and after(2) == "uncertain" and after(3) == "uncertain"
    # Beta(1, 5): the 80% interval is 0.348 wide, just under the 0.35 threshold.
    assert after(4) == "weak" and after(8) == "weak"


def test_a_few_successes_are_not_mastery() -> None:
    result = estimate(FRACTIONS, successes(3))
    assert result.mean == pytest.approx(0.8) and result.status == "uncertain"


# ----- mastery needs more than a high mean ---------------------------------------------


def test_mastery_needs_enough_evidence_attempts_contexts_and_autonomy() -> None:
    assert estimate(FRACTIONS, successes(9)).status == "mastered"
    assert estimate(FRACTIONS, successes(9, attempts=3)).lower >= 0.75

    # One context only: nine items, but all inside the same case.
    one_context = estimate(FRACTIONS, successes(9, evidence={"context": "case-1"}))
    assert one_context.distinct_contexts == 1 and one_context.status == "developing"

    # Two attempts only, with the per-attempt cap lifted so the interval alone would qualify.
    loose = MasteryParams(attempt_weight_cap=10.0)
    two_attempts = estimate(FRACTIONS, successes(10, attempts=2), params=loose)
    assert two_attempts.lower >= 0.75 and two_attempts.status == "developing"
    assert estimate(FRACTIONS, successes(10, attempts=3), params=loose).status == "mastered"

    # Everything shown with help: it counts as evidence, not as autonomy.
    helped = estimate(FRACTIONS, successes(9, kind="assisted"))
    assert helped.effective_n == pytest.approx(9.0) and helped.autonomous_n == 0.0
    assert helped.status == "developing"


def test_a_recent_failure_on_a_critical_skill_blocks_mastery_for_fourteen_days() -> None:
    loose = MasteryParams(attempt_weight_cap=30.0)
    history = [
        *successes(20, skill="exam_rules"),
        obs("exam_rules", kind="failure", item="slip", attempt="a-slip", days=1.0, id="z-slip"),
    ]
    assert estimate(EXAM_RULES, history, days=2.0, params=loose).status == "developing"
    # The same history on a skill that is not critical is mastered.
    ordinary = replace(EXAM_RULES, critical=False)
    assert estimate(ordinary, history, days=2.0, params=loose).status == "mastered"
    # Fourteen days after the failure the block lifts.
    assert estimate(EXAM_RULES, history, days=15.5, params=loose).status == "mastered"


def test_required_omissions_count_as_failures_on_a_critical_skill() -> None:
    loose = MasteryParams(attempt_weight_cap=30.0)
    history = [
        *successes(20, skill="exam_rules"),
        obs("exam_rules", kind="required_omission", item="skip", attempt="a-skip", days=1.0),
    ]
    assert estimate(EXAM_RULES, history, days=2.0, params=loose).status != "mastered"


# ----- forgetting and recheck -------------------------------------------------------------


def test_evidence_loses_half_its_weight_every_half_life() -> None:
    history = successes(9)
    at_last = estimate(FRACTIONS, history)
    later = estimate(FRACTIONS, history, days=45.0)
    assert later.effective_n == pytest.approx(at_last.effective_n / 2, rel=1e-12)
    assert later.autonomous_n == pytest.approx(at_last.autonomous_n / 2, rel=1e-12)
    # Forgetting pulls the mean back to the prior and widens the interval.
    assert 0.5 < later.mean < at_last.mean
    assert later.upper - later.lower > at_last.upper - at_last.lower
    assert later.last_observed_at == at_last.last_observed_at


def test_a_mastered_skill_is_due_for_a_recheck_after_one_half_life_without_evidence() -> None:
    history = successes(9)
    assert estimate(FRACTIONS, history, days=44.0).status == "mastered"
    stale = estimate(FRACTIONS, history, days=46.0)
    # The numbers have faded honestly; the status says what to do about it.
    assert stale.status == "recheck" and stale.lower < 0.75


def test_a_skill_that_was_never_mastered_does_not_become_a_recheck() -> None:
    assert estimate(FRACTIONS, successes(3), days=200.0).status != "recheck"


# ----- which observations count, and how much ---------------------------------------------


@pytest.mark.parametrize("kind", ["not_elicited", "uninterpretable", "interrupted"])
def test_silent_observations_change_nothing_even_when_recent_and_reliable(kind: str) -> None:
    history = successes(5)
    quiet = obs("fractions", kind=kind, item="x", attempt="late", days=30.0, reliability=1.0)  # type: ignore[arg-type]
    assert estimate(FRACTIONS, history, days=31.0) == estimate(
        FRACTIONS, [*history, quiet], days=31.0
    )
    assert estimate(FRACTIONS, [quiet]).status == "unknown"


def test_a_technical_error_never_becomes_a_negative_observation() -> None:
    result = estimate(FRACTIONS, [obs("fractions", kind="uninterpretable", outcome=0.0)])
    assert result.status == "unknown" and result.effective_n == 0.0


def test_reliability_scales_the_weight() -> None:
    assert estimate(FRACTIONS, [obs("fractions", reliability=0.5)]).effective_n == 0.5
    assert estimate(FRACTIONS, [obs("fractions", reliability=0.0)]).status == "unknown"


def test_possibly_mistranscribed_evidence_weighs_half() -> None:
    plain = estimate(FRACTIONS, [obs("fractions")])
    suspect = estimate(FRACTIONS, [obs("fractions", kind="stt_suspect", outcome=1.0)])
    assert suspect.effective_n == pytest.approx(plain.effective_n / 2)


def test_seeing_the_same_item_again_counts_half_and_is_one_item() -> None:
    twice = estimate(
        FRACTIONS,
        [obs("fractions", item="x", attempt="a1"), obs("fractions", item="x", attempt="a2")],
    )
    assert twice.effective_n == pytest.approx(1.5) and twice.distinct_items == 1


def test_one_attempt_cannot_add_more_than_the_cap_to_a_skill() -> None:
    one_long_session = estimate(FRACTIONS, successes(10, attempts=1))
    assert one_long_session.effective_n == pytest.approx(3.0)
    assert one_long_session.status != "mastered"


def test_a_reassessment_replaces_the_earlier_assessment_it_does_not_add_to_it() -> None:
    first = obs("fractions", kind="failure", attempt="a1", item="i1", producer="eval-v6", id="a")
    second = obs(
        "fractions", kind="success", attempt="a1", item="i1", producer="eval-v6", days=1, id="b"
    )
    redone = estimate(FRACTIONS, [first, second], days=1.0)
    assert redone.effective_n == pytest.approx(1.0)
    assert redone == estimate(FRACTIONS, [second], days=1.0)
    # Another rubric item of the same attempt is another piece of evidence: it adds.
    keyed = [
        obs(
            "fractions",
            attempt="a1",
            producer="eval-v6",
            item="i1",
            evidence={"evidence_key": "k1"},
        ),
        obs(
            "fractions",
            attempt="a1",
            producer="eval-v6",
            item="i2",
            evidence={"evidence_key": "k2"},
        ),
    ]
    assert estimate(FRACTIONS, keyed).effective_n == pytest.approx(2.0)
    # Another producer assessing the same performance is other evidence, but of the same item
    # in the same attempt: the second is damped like a repeat, it is not full independent proof.
    other = [first, obs("fractions", kind="success", attempt="a1", item="i1", producer="other-v1")]
    assert estimate(FRACTIONS, other).effective_n == pytest.approx(1.5)
    # Several items inside one attempt are all evidence: nothing collapses by itself.
    exam = [obs("fractions", attempt="exam", item=f"q{n}") for n in range(3)]
    assert estimate(FRACTIONS, exam).effective_n == pytest.approx(3.0)


def test_a_silent_reassessment_removes_the_assessment_it_replaces() -> None:
    first = obs("fractions", kind="failure", attempt="a1", producer="eval-v6", id="a")
    unreadable = obs(
        "fractions", kind="uninterpretable", attempt="a1", producer="eval-v6", days=1, id="b"
    )
    assert estimate(FRACTIONS, [first, unreadable], days=1.0).status == "unknown"


def test_the_future_is_not_visible_and_replays_are_as_of_now() -> None:
    history = successes(4)
    later = obs("fractions", kind="failure", item="late", attempt="late", days=10.0)
    assert estimate(FRACTIONS, [*history, later], days=5.0) == estimate(
        FRACTIONS, history, days=5.0
    )
    assert estimate(FRACTIONS, [later]).status == "unknown"


def test_observations_of_other_skills_are_ignored() -> None:
    assert estimate(FRACTIONS, successes(5, skill="arithmetic")).status == "unknown"


def test_the_order_of_the_input_does_not_matter() -> None:
    history = [*successes(9), obs("fractions", kind="failure", item="f", attempt="af", days=3.0)]
    forward = estimate(FRACTIONS, history, days=10.0)
    for seed in range(5):
        shuffled = history[:]
        random.Random(seed).shuffle(shuffled)
        assert estimate(FRACTIONS, shuffled, days=10.0) == forward


def test_the_prior_counts_are_not_forgotten_away() -> None:
    far = estimate(FRACTIONS, successes(9), days=10_000.0)
    assert far.mean == pytest.approx(0.5, abs=1e-6) and far.effective_n < 1e-6


# ----- validation -------------------------------------------------------------------------


def test_observations_and_parameters_refuse_nonsense() -> None:
    with pytest.raises(ValueError, match="timezone"):
        obs("fractions", at=T0.replace(tzinfo=None))
    with pytest.raises(ValueError, match="reliability"):
        obs("fractions", reliability=1.5)
    with pytest.raises(ValueError, match="outcome"):
        obs("fractions", outcome=1.5)
    with pytest.raises(ValueError, match="needs an outcome"):
        Observation("x", "l", "s", "i", "a", "success", None, 1.0, "llm", "p", {}, T0)
    with pytest.raises(ValueError):
        MasteryParams(prior_alpha=0.0)
    with pytest.raises(ValueError):
        MasteryParams(interval_mass=1.0)
    with pytest.raises(ValueError):
        MasteryParams(repeat_item_factor=2.0)
    with pytest.raises(ValueError, match="timezone"):
        MODEL.estimate(FRACTIONS, [], T0.replace(tzinfo=None))


# ----- properties over random histories ---------------------------------------------------

KINDS = ["success", "failure", "required_omission", "not_elicited", "uninterpretable",
         "stt_suspect", "interrupted", "assisted"]  # fmt: skip


def random_history(rng: random.Random) -> list[Observation]:
    history = []
    for n in range(rng.randint(0, 40)):
        kind = rng.choice(KINDS)
        silent = kind in {"not_elicited", "uninterpretable", "interrupted"}
        outcome = None if silent else rng.choice([0.0, 1.0, rng.random()])
        history.append(
            obs(
                "fractions",
                kind=kind,  # type: ignore[arg-type]
                outcome=outcome,
                reliability=rng.choice([0.2, 0.5, 1.0]),
                item=f"i{rng.randint(0, 12)}",
                attempt=f"a{rng.randint(0, 8)}",
                days=rng.uniform(0, 120),
                id=f"r{n:03d}",
                evidence={
                    "evidence_key": f"k{rng.randint(0, 3)}",
                    "context": f"c{rng.randint(0, 3)}",
                },
            )
        )
    return history


def test_every_estimate_is_coherent_whatever_the_history() -> None:
    rng = random.Random(20261019)
    for _ in range(300):
        history = random_history(rng)
        result = estimate(FRACTIONS, history, days=rng.uniform(100, 400))
        assert 0.0 <= result.lower <= result.mean <= result.upper <= 1.0
        assert result.effective_n >= 0.0 and 0.0 <= result.autonomous_n <= result.effective_n + 1e-9
        assert result.distinct_items <= 13 and result.distinct_contexts <= 4
        assert result.status in {
            "unknown",
            "weak",
            "developing",
            "mastered",
            "recheck",
            "uncertain",
        }
        assert (result.status == "unknown") == (result.last_observed_at is None)


def test_replaying_gives_the_same_answer_every_time() -> None:
    rng = random.Random(7)
    for _ in range(50):
        history = random_history(rng)
        now = rng.uniform(0, 200)
        assert estimate(FRACTIONS, history, days=now) == estimate(
            FRACTIONS, list(history), days=now
        )


def test_a_new_success_never_lowers_the_mean_and_a_new_failure_never_raises_it() -> None:
    rng = random.Random(11)
    for _ in range(300):
        history = random_history(rng)
        moment = rng.uniform(120, 200)  # after everything already observed
        before = estimate(FRACTIONS, history, days=moment)
        fresh = {"item": "brand-new", "attempt": "brand-new", "days": moment, "id": "zzz"}
        reliability = rng.choice([0.2, 0.5, 1.0])
        better = estimate(
            FRACTIONS, [*history, obs("fractions", reliability=reliability, **fresh)], days=moment
        )
        worse = estimate(
            FRACTIONS,
            [*history, obs("fractions", kind="failure", reliability=reliability, **fresh)],
            days=moment,
        )
        assert better.mean >= before.mean - 1e-12
        assert worse.mean <= before.mean + 1e-12
        assert better.effective_n > before.effective_n - 1e-12


def test_evidence_only_fades_as_time_passes_without_new_observations() -> None:
    rng = random.Random(3)
    for _ in range(100):
        history = random_history(rng)
        sizes = [estimate(FRACTIONS, history, days=d).effective_n for d in (130, 160, 220, 400)]
        assert sizes == sorted(sizes, reverse=True)


def test_assessing_the_same_evidence_twice_is_the_same_as_once() -> None:
    rng = random.Random(5)
    for _ in range(100):
        history = random_history(rng)
        twins = [
            replace(o, id=f"{o.id}-again")  # same instant, same key: the later id replaces
            for o in history
        ]
        assert estimate(FRACTIONS, [*history, *twins], days=150) == estimate(
            FRACTIONS, history, days=150
        )
