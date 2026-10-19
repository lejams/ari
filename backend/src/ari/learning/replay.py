"""Replay: the mastery of every skill of a framework, recomputed from the observations.

The observations are the source of truth, the estimates are a projection. Changing the model
version or its parameters therefore never loses anything: replay the same observations with
the new ones. The function is deterministic: the same arguments always give the same result,
whatever order the observations arrive in.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime

from ari.learning.framework import SkillFramework
from ari.learning.mastery import MODEL_VERSION, BetaMasteryModel, MasteryModel, MasteryParams
from ari.learning.model import MasteryEstimate, Observation


class UnknownModelVersion(ValueError):
    """No mastery model has this version."""


def mastery_model(version: str, params: MasteryParams) -> MasteryModel:
    if version == MODEL_VERSION:
        return BetaMasteryModel(params)
    raise UnknownModelVersion(f"unknown mastery model version: {version}")


def replay(
    observations: Iterable[Observation],
    framework: SkillFramework,
    model_version: str,
    params: MasteryParams,
    now: datetime,
    *,
    priors: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, MasteryEstimate]:
    """One learner's estimate for every skill of the framework, as of `now`.

    Observations of a skill the framework does not know (a taxonomy that evolved) are ignored:
    they stay stored and count again if the skill comes back. Mixing learners is an error.
    """
    model = mastery_model(model_version, params)
    by_skill: dict[str, list[Observation]] = defaultdict(list)
    learners: set[str] = set()
    known = framework.skill_ids
    for observation in observations:
        learners.add(observation.learner_id)
        if observation.skill_id in known:
            by_skill[observation.skill_id].append(observation)
    if len(learners) > 1:
        raise ValueError(f"replay takes one learner's observations, got {sorted(learners)}")
    given = priors or {}
    return {
        skill.id: model.estimate(skill, by_skill.get(skill.id, ()), now, prior=given.get(skill.id))
        for skill in framework.skills
    }
