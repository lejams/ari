"""Replay, and a whole learning cycle in a domain that has nothing to do with the FSP."""

from __future__ import annotations

import dataclasses
import random
from datetime import timedelta

import pytest
from toy_domain import T0, ToyAnswer, obs, toy_adapter, toy_framework

from ari.learning import MODEL_VERSION, MasteryParams, UnknownModelVersion, replay

PARAMS = MasteryParams()


def simulate_pupil(true_level: dict[str, float], days: int, seed: int) -> list:  # type: ignore[type-arg]
    """A pupil who answers one exercise per skill per day, right with a fixed probability."""
    rng = random.Random(seed)
    observations = []
    for day in range(days):
        for skill, probability in true_level.items():
            answer = ToyAnswer(
                pupil="pupil-1",
                exercise=f"{skill}-{day}",
                skills=(skill,),
                correct=rng.random() < probability,
                attempt=f"attempt-{skill}-{day}",
                at=T0 + timedelta(days=day, hours=2),
            )
            observations.extend(toy_adapter(answer))
    return observations


TRUE_LEVEL = {
    "arithmetic": 0.95,
    "fractions": 0.85,
    "equations": 0.25,
    "functions": 0.10,
    "exam_rules": 0.90,
}


def test_a_full_cycle_in_a_second_domain_finds_the_gaps_and_does_not_overstate_the_strengths() -> (
    None
):
    framework = toy_framework()
    observations = simulate_pupil(TRUE_LEVEL, days=16, seed=42)
    profile = replay(observations, framework, MODEL_VERSION, PARAMS, T0 + timedelta(days=16))

    assert set(profile) == framework.skill_ids
    assert profile["equations"].status == "weak" and profile["functions"].status == "weak"
    assert profile["arithmetic"].status == "mastered"
    assert profile["arithmetic"].lower >= 0.75
    for skill in ("arithmetic", "fractions", "exam_rules"):
        assert profile[skill].status in {"mastered", "developing"}
        assert profile[skill].mean > profile["equations"].mean
    # Honest about what it knows: the interval is always there and always brackets the mean.
    assert all(0.0 <= e.lower <= e.mean <= e.upper <= 1.0 for e in profile.values())


def test_the_profile_tracks_the_pupil_as_evidence_arrives() -> None:
    framework = toy_framework()
    observations = simulate_pupil({"fractions": 0.9}, days=16, seed=1)
    statuses = []
    for day in (1, 3, 6, 10, 16):
        known = [o for o in observations if o.observed_at <= T0 + timedelta(days=day)]
        statuses.append(
            replay(known, framework, MODEL_VERSION, PARAMS, T0 + timedelta(days=day))[
                "fractions"
            ].status
        )
    # Little evidence says "uncertain", never "mastered"; it takes time to earn the word.
    assert statuses[0] == "uncertain" and "mastered" not in statuses[:2]
    assert statuses[-1] in {"mastered", "developing"}


def test_a_skill_that_stops_being_practised_is_asked_to_be_rechecked() -> None:
    framework = toy_framework()
    observations = simulate_pupil({"arithmetic": 1.0}, days=12, seed=3)
    last = T0 + timedelta(days=12)
    assert (
        replay(observations, framework, MODEL_VERSION, PARAMS, last)["arithmetic"].status
        == "mastered"
    )
    later = last + timedelta(days=75)  # beyond the 60-day half-life of arithmetic
    assert (
        replay(observations, framework, MODEL_VERSION, PARAMS, later)["arithmetic"].status
        == "recheck"
    )


def test_replay_is_deterministic_and_independent_of_the_order_of_the_observations() -> None:
    framework = toy_framework()
    observations = simulate_pupil(TRUE_LEVEL, days=10, seed=9)
    now = T0 + timedelta(days=10)
    reference = replay(observations, framework, MODEL_VERSION, PARAMS, now)
    assert replay(list(observations), framework, MODEL_VERSION, PARAMS, now) == reference
    shuffled = observations[:]
    random.Random(0).shuffle(shuffled)
    assert replay(shuffled, framework, MODEL_VERSION, PARAMS, now) == reference
    assert replay(iter(shuffled), framework, MODEL_VERSION, PARAMS, now) == reference


def test_changing_the_parameters_recomputes_the_profile_and_leaves_the_observations_alone() -> None:
    framework = toy_framework()
    observations = simulate_pupil({"arithmetic": 1.0}, days=12, seed=5)  # a flawless pupil
    snapshot = list(observations)
    now = T0 + timedelta(days=12)
    cautious = replay(
        observations, framework, MODEL_VERSION, MasteryParams(mastered_lower=0.9), now
    )
    standard = replay(observations, framework, MODEL_VERSION, PARAMS, now)
    assert (
        cautious["arithmetic"].status != "mastered" and standard["arithmetic"].status == "mastered"
    )
    assert observations == snapshot
    with pytest.raises(dataclasses.FrozenInstanceError):
        observations[0].reliability = 0.0  # type: ignore[misc]


def test_observations_of_skills_the_taxonomy_dropped_are_ignored_not_fatal() -> None:
    framework = toy_framework()
    kept = [obs("fractions", item="a"), obs("fractions", item="b", attempt="a2")]
    profile = replay([*kept, obs("retired-skill", item="c")], framework, MODEL_VERSION, PARAMS, T0)
    assert "retired-skill" not in profile
    assert profile == replay(kept, framework, MODEL_VERSION, PARAMS, T0)


def test_replay_takes_one_learner_and_a_known_model_version() -> None:
    framework = toy_framework()
    with pytest.raises(ValueError, match="one learner"):
        replay(
            [obs("fractions", learner="a"), obs("fractions", learner="b")],
            framework,
            MODEL_VERSION,
            PARAMS,
            T0,
        )
    with pytest.raises(UnknownModelVersion):
        replay([], framework, "mastery-beta-v9", PARAMS, T0)


def test_priors_from_a_placement_test_start_the_skills_somewhere_else() -> None:
    framework = toy_framework()
    profile = replay([], framework, MODEL_VERSION, PARAMS, T0, priors={"fractions": (8.0, 2.0)})
    assert profile["fractions"].mean == pytest.approx(0.8)
    assert profile["arithmetic"].mean == pytest.approx(0.5)
    assert all(e.status == "unknown" for e in profile.values())
