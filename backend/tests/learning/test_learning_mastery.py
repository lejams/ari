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


def test_a_critical_failure_blocks_mastery_even_past_the_attempt_cap() -> None:
    history = successes(9, skill="exam_rules")
    capped = [
        *[obs("exam_rules", item=f"c{n}", attempt="full", days=9.0) for n in range(3)],
        obs("exam_rules", kind="failure", item="slip", attempt="full", days=9.5),
    ]
    assert estimate(EXAM_RULES, history, days=10.0).status == "mastered"
    assert estimate(EXAM_RULES, [*history, *capped], days=10.0).status not in {
        "mastered",
        "recheck",
    }
    # Evidence of reliability 0 is no evidence: it neither counts nor blocks.
    worthless = obs("exam_rules", kind="failure", item="z", attempt="z", days=9.5, reliability=0.0)
    assert estimate(EXAM_RULES, [*history, worthless], days=10.0).status == "mastered"


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


def test_a_mastered_skill_fades_into_a_recheck_never_into_developing() -> None:
    history = successes(9)
    assert estimate(FRACTIONS, history).status == "mastered"
    for days in (1.0, 5.0, 20.0, 44.0, 46.0, 200.0):
        later = estimate(FRACTIONS, history, days=days)
        assert later.status in {"mastered", "recheck"}
        # The status never contradicts the numbers next to it.
        assert later.status != "mastered" or later.lower >= 0.75
    stale = estimate(FRACTIONS, history, days=46.0)
    assert stale.status == "recheck" and stale.lower < 0.75


def test_after_one_half_life_without_evidence_even_strong_mastery_is_due_for_a_recheck() -> None:
    loose = MasteryParams(attempt_weight_cap=30.0)
    history = successes(60, attempts=20)
    assert estimate(FRACTIONS, history, days=44.0, params=loose).status == "mastered"
    stale = estimate(FRACTIONS, history, days=46.0, params=loose)
    assert stale.lower >= 0.75 and stale.status == "recheck"


def test_a_new_complete_success_never_takes_mastery_away() -> None:
    history = successes(8)
    for days in (5.0, 10.0, 20.0, 31.0, 45.0):
        before = estimate(FRACTIONS, history, days=days)
        fresh = obs("fractions", item="fresh", attempt="fresh", days=days, id="zzz")
        after = estimate(FRACTIONS, [*history, fresh], days=days)
        assert before.status in {"mastered", "recheck"}
        assert after.status == "mastered" or (before.status, after.status) == ("recheck", "recheck")


def test_a_partial_success_can_turn_mastery_into_a_recheck_but_no_lower() -> None:
    # A success scored 0.6 is not proof of mastery at 0.75: the status may fall to a recheck,
    # never further, since the skill has lost nothing but certainty.
    history = successes(8)
    assert estimate(FRACTIONS, history, days=2.0).status == "mastered"
    for outcome, expected in ((1.0, "mastered"), (0.6, "recheck")):
        partial = obs("fractions", outcome=outcome, item="p", attempt="p", days=2.0)
        assert estimate(FRACTIONS, [*history, partial], days=2.0).status == expected


@pytest.mark.parametrize("kind", ["assisted", "stt_suspect"])
def test_a_miss_of_any_kind_after_mastery_is_not_a_recheck(kind: str) -> None:
    # Missing even with help, or a miss that may be mistranscribed, is still a loss, not time.
    misses = [
        obs("fractions", kind=kind, outcome=0.0, item=f"m{n}", attempt=f"m{n}", days=10.0)  # type: ignore[arg-type]
        for n in range(20)
    ]
    result = estimate(FRACTIONS, [*successes(8), *misses], days=11.0)
    assert result.mean < 0.5 and result.status not in {"mastered", "recheck"}


def test_a_failure_after_mastery_is_not_a_recheck_it_is_read_on_the_numbers() -> None:
    history = [
        *successes(8),
        *[
            obs("fractions", kind="failure", item=f"f{n}", attempt=f"f{n}", days=1.0)
            for n in range(6)
        ],
    ]
    assert estimate(FRACTIONS, history, days=2.0).status not in {"mastered", "recheck"}


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


def test_the_repeat_damping_fades_as_the_earlier_sighting_is_forgotten() -> None:
    # The next day, a repeated item still counts about half.
    next_day = estimate(
        FRACTIONS,
        [
            obs("fractions", item="x", attempt="a1"),
            obs("fractions", item="x", attempt="a2", days=1.0),
        ],
        days=1.0,
    )
    remembered = 0.5 ** (1 / 45)
    assert next_day.effective_n == pytest.approx(remembered + 1 - 0.5 * remembered)
    # Months later it is a genuine review: having done the items before never counts against.
    recent = [obs("fractions", item=f"i{n}", attempt=f"r{n}", days=100.0) for n in range(9)]
    early = [obs("fractions", item=f"i{n}", attempt=f"e{n}") for n in range(9)]
    alone = estimate(FRACTIONS, recent, days=100.0)
    reviewed = estimate(FRACTIONS, [*early, *recent], days=100.0)
    assert alone.status == reviewed.status == "mastered"
    assert reviewed.mean >= alone.mean and reviewed.effective_n >= alone.effective_n


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
    # Another producer assessing the same performance is other evidence; the per-attempt cap
    # bounds it. Whether a new model version should replace instead is the adapter's call.
    other = [first, obs("fractions", kind="success", attempt="a1", item="i1", producer="other-v1")]
    assert estimate(FRACTIONS, other).effective_n == pytest.approx(2.0)
    # Several items inside one attempt are all evidence: nothing collapses by itself.
    exam = [obs("fractions", attempt="exam", item=f"q{n}") for n in range(3)]
    assert estimate(FRACTIONS, exam).effective_n == pytest.approx(3.0)


@pytest.mark.parametrize("kind", ["uninterpretable", "interrupted"])
def test_a_technical_error_on_reassessment_keeps_the_assessment(kind: str) -> None:
    # A reassessment that fails technically must not change what is known about the learner.
    first = [
        obs("fractions", kind="failure", attempt=f"a{n}", item=f"i{n}", producer="eval-v6")
        for n in range(5)
    ]
    unreadable = [
        obs("fractions", kind=kind, attempt=f"a{n}", item=f"i{n}", producer="eval-v6",  # type: ignore[arg-type]
            days=1.0)
        for n in range(5)
    ]  # fmt: skip
    assert estimate(FRACTIONS, [*first, *unreadable], days=1.0) == estimate(
        FRACTIONS, first, days=1.0
    )


def test_a_not_elicited_reassessment_withdraws_a_wrongly_attributed_result() -> None:
    loose = MasteryParams(attempt_weight_cap=30.0)
    history = successes(20, skill="exam_rules")
    wrong = obs("exam_rules", kind="failure", item="slip", attempt="a-slip", producer="eval-v6",
                days=9.0)  # fmt: skip
    withdrawn = replace(
        obs("exam_rules", kind="not_elicited", item="slip", attempt="a-slip", producer="eval-v6",
            days=9.0),
        assessed_at=T0 + timedelta(days=9.5),
    )  # fmt: skip
    clean = estimate(EXAM_RULES, history, days=10.0, params=loose)
    assert clean.status == "mastered"
    # The wrong failure blocks the critical skill until it is withdrawn, then leaves no trace.
    assert estimate(EXAM_RULES, [*history, wrong], days=10.0, params=loose).status != "mastered"
    assert estimate(EXAM_RULES, [*history, wrong, withdrawn], days=10.0, params=loose) == clean
    # Before the withdrawal was made, a replay still sees the failure.
    before = estimate(EXAM_RULES, [*history, wrong, withdrawn], days=9.2, params=loose)
    assert before.status != "mastered"


def test_the_reassessment_assessed_last_wins_whatever_the_ids() -> None:
    def pair(old_id: str, new_id: str) -> list[Observation]:
        original = obs("fractions", kind="failure", attempt="a1", producer="eval-v6", id=old_id)
        redone = replace(
            obs("fractions", kind="success", attempt="a1", producer="eval-v6", id=new_id),
            assessed_at=T0 + timedelta(days=1),
        )
        return [original, redone]

    for ids in (("a", "b"), ("b", "a"), ("9f", "1a")):
        result = estimate(FRACTIONS, pair(*ids), days=2.0)
        assert result.mean > 0.5 and result.effective_n == pytest.approx(1.0 * 0.5 ** (2 / 45))
    # The evidence keeps the instant of the attempt; before the reassessment, the original holds.
    assert estimate(FRACTIONS, pair("a", "b"), days=0.5).mean < 0.5
    assert estimate(FRACTIONS, pair("a", "b"), days=2.0).last_observed_at == T0


def test_several_rubric_items_of_one_attempt_count_alike_whatever_the_ids() -> None:
    def rubric(ok_id: str, ko_id: str) -> list[Observation]:
        return [
            obs("fractions", item="case-1", attempt="att",
                evidence={"evidence_key": "r3"}, id=ok_id),
            obs("fractions", kind="failure", item="case-1", attempt="att",
                evidence={"evidence_key": "r7"}, id=ko_id),
        ]  # fmt: skip

    forward, backward = estimate(FRACTIONS, rubric("a", "b")), estimate(FRACTIONS, rubric("b", "a"))
    assert forward == backward and forward.mean == pytest.approx(0.5)
    assert forward.effective_n == pytest.approx(2.0)


def test_the_cap_scales_a_whole_attempt_evenly() -> None:
    session = [
        *[obs("fractions", item=f"ok{n}", attempt="long") for n in range(4)],
        *[obs("fractions", kind="failure", item=f"ko{n}", attempt="long") for n in range(2)],
    ]
    result = estimate(FRACTIONS, session)
    assert result.effective_n == pytest.approx(3.0)
    assert result.mean == pytest.approx((1 + 2.0) / (2 + 3.0))  # 4/6 of 3 is 2 successes


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
    with pytest.raises(ValueError, match="reliability"):
        obs("fractions", reliability=True)
    with pytest.raises(ValueError, match="assessed_at"):
        replace(obs("fractions", days=1.0), assessed_at=T0)
    with pytest.raises(ValueError, match="assessed_at"):
        replace(obs("fractions"), assessed_at=T0.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("kind", "outcome"), [("failure", 1.0), ("required_omission", 0.8), ("success", 0.0)]
)
def test_the_kind_and_the_outcome_must_agree(kind: str, outcome: float) -> None:
    with pytest.raises(ValueError, match="outcome"):
        obs("fractions", kind=kind, outcome=outcome)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad",
    [
        {"mastered_lower": float("nan")},
        {"prior_alpha": float("nan")},
        {"attempt_weight_cap": float("inf")},
        {"weak_min_n": -1.0},
        {"min_attempts_for_mastery": 0},
        {"uncertain_width": 1.5},
    ],
)
def test_parameters_must_be_finite_and_in_range(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        MasteryParams(**bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("half_life", [float("nan"), float("inf"), 0.0])
def test_a_half_life_must_be_finite_and_positive(half_life: float) -> None:
    with pytest.raises(ValueError, match="half_life_days"):
        Skill("s", "S", "f", half_life_days=half_life)


@pytest.mark.parametrize("prior", [(0.0, 1.0), (float("nan"), 1.0), (1.0, float("inf"))])
def test_a_prior_must_be_finite_and_positive(prior: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="prior"):
        estimate(FRACTIONS, [], prior=prior)


# ----- properties over random histories ---------------------------------------------------

KINDS = ["success", "failure", "required_omission", "not_elicited", "uninterpretable",
         "stt_suspect", "interrupted", "assisted"]  # fmt: skip


def random_history(rng: random.Random) -> list[Observation]:
    history = []
    for n in range(rng.randint(0, 40)):
        kind = rng.choice(KINDS)
        if kind in {"not_elicited", "uninterpretable", "interrupted"}:
            outcome = None
        elif kind in {"failure", "required_omission"}:
            outcome = rng.choice([0.0, rng.uniform(0.0, 0.5)])
        elif kind == "success":
            outcome = rng.choice([1.0, rng.uniform(0.5, 1.0)])
        else:
            outcome = rng.choice([0.0, 1.0, rng.random()])
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
        assert result.status != "mastered" or result.lower >= 0.75


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
        # Nor does a new complete success take mastery away, or turn a recheck into less.
        if before.status == "mastered":
            assert better.status == "mastered"
        if before.status == "recheck":
            assert better.status in {"recheck", "mastered"}


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
