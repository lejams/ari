"""`mastery-beta-v1`: a Beta model of mastery per skill, with forgetting and honest uncertainty.

Each skill starts from a prior Beta(alpha0, beta0) (uniform by default). An observation with
outcome o and weight w moves the counts: alpha += w * o, beta += w * (1 - o). The weight is the
observation's reliability, reduced when an earlier attempt already showed the same item (less
so as that sighting is forgotten) and when the evidence may come from a transcription error.
The observations of one attempt are scaled together so that one long session adds at most a
capped weight to a skill.

Between observations the evidence fades: the counts above the prior lose half their weight
every `half_life_days` of the skill. The estimate is the mean of the Beta with a central
credible interval, and the status is read from the lower bound as it stands now, so the model
errs on the side of not overstating what a learner can do. A skill that was mastered and has
since lost nothing but time is due for a recheck.

Everything is recomputed from the list of observations: there is no incremental state to drift,
and replaying twice, in any input order, gives the same result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from itertools import groupby
from math import isfinite
from typing import Protocol

from ari.learning.beta import beta_mean, beta_ppf
from ari.learning.model import (
    FAILING_KINDS,
    SILENT_KINDS,
    TECHNICAL_KINDS,
    MasteryEstimate,
    MasteryStatus,
    Observation,
    Skill,
)

MODEL_VERSION = "mastery-beta-v1"


@dataclass(frozen=True, slots=True)
class MasteryParams:
    """Every tunable of the model. The defaults are the reference values of version 1: they
    are hypotheses to calibrate against the evaluation datasets, and the application passes
    the values it versions in its own configuration."""

    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    interval_mass: float = 0.80  # mass of the central credible interval
    attempt_weight_cap: float = 3.0  # most weight one attempt may add to one skill
    repeat_item_factor: float = 0.5  # weight kept when an earlier attempt just showed the item
    stt_suspect_factor: float = 0.5  # weight kept when the evidence may be a transcription error
    mastered_lower: float = 0.75  # lower bound of mastery needed to call a skill mastered
    min_attempts_for_mastery: int = 3
    min_contexts_for_mastery: int = 2
    min_autonomous_weight: float = 1.0  # weight shown without help
    critical_window_days: float = 14.0  # a failure on a critical skill blocks mastery this long
    uncertain_width: float = 0.35  # interval wider than this: too uncertain to say
    weak_mean: float = 0.5
    weak_min_n: float = 2.0

    def __post_init__(self) -> None:
        for parameter in fields(self):
            value = getattr(self, parameter.name)
            if isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"{parameter.name} must be a finite number")
        if self.prior_alpha <= 0 or self.prior_beta <= 0:
            raise ValueError("the prior needs positive counts")
        for name in ("mastered_lower", "uncertain_width", "weak_mean"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} lies in [0, 1]")
        for name in ("min_autonomous_weight", "critical_window_days", "weak_min_n"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.min_attempts_for_mastery < 1 or self.min_contexts_for_mastery < 1:
            raise ValueError("mastery needs at least one attempt and one context")
        if not 0.0 < self.interval_mass < 1.0:
            raise ValueError("interval_mass lies in (0, 1)")
        for name in ("repeat_item_factor", "stt_suspect_factor"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} lies in [0, 1]")
        if self.attempt_weight_cap <= 0:
            raise ValueError("attempt_weight_cap must be positive")


class MasteryModel(Protocol):
    version: str

    def estimate(
        self,
        skill: Skill,
        observations: Iterable[Observation],
        now: datetime,
        *,
        prior: tuple[float, float] | None = None,
    ) -> MasteryEstimate: ...


def _fade(elapsed: timedelta, half_life_days: float) -> float:
    return float(0.5 ** (elapsed.total_seconds() / (half_life_days * 86_400.0)))


class BetaMasteryModel:
    version = MODEL_VERSION

    def __init__(self, params: MasteryParams | None = None) -> None:
        self.params = params or MasteryParams()

    def estimate(
        self,
        skill: Skill,
        observations: Iterable[Observation],
        now: datetime,
        *,
        prior: tuple[float, float] | None = None,
    ) -> MasteryEstimate:
        if now.tzinfo is None:
            raise ValueError("now needs a timezone")
        params = self.params
        alpha0, beta0 = prior or (params.prior_alpha, params.prior_beta)
        if not all(isfinite(c) and c > 0 for c in (alpha0, beta0)):
            raise ValueError(f"{skill.id}: the prior needs finite positive counts")
        tail = (1.0 - params.interval_mass) / 2.0

        # Only what was known at `now`. A technical error neither counts nor replaces an earlier
        # assessment; a `not_elicited` reassessment replaces it, then counts for nothing, which
        # is how a wrong attribution is withdrawn. Of two assessments of the same evidence, the
        # one assessed last wins.
        latest: dict[tuple[str, str, str, str, str], Observation] = {}
        for observation in sorted(
            (
                o
                for o in observations
                if o.skill_id == skill.id
                and o.observed_at <= now
                and o.assessed <= now
                and o.kind not in TECHNICAL_KINDS
            ),
            key=lambda o: (o.assessed, o.id),
        ):
            latest[observation.replacement_key] = observation
        evidence = [
            o for o in latest.values() if o.kind not in SILENT_KINDS and o.reliability > 0.0
        ]

        # A failure on a critical skill blocks mastery whatever weight it ends up with.
        critical_failures = [o.observed_at for o in evidence if o.kind in FAILING_KINDS]

        # Weights, independent of the order of the input and of the ids. An item counts less when
        # an earlier attempt already showed it, and the less so the more that sighting has been
        # forgotten: redoing an item the next day proves little, redoing it months later is a
        # genuine review. The observations of one attempt are scaled together so that the
        # attempt adds at most the cap.
        attempt_start: dict[str, datetime] = {}
        for o in evidence:
            if o.attempt_ref not in attempt_start or o.observed_at < attempt_start[o.attempt_ref]:
                attempt_start[o.attempt_ref] = o.observed_at
        rank = {a: (start, a) for a, start in attempt_start.items()}
        sightings: dict[str, list[tuple[tuple[datetime, str], datetime]]] = {}
        for o in evidence:
            sightings.setdefault(o.item_ref, []).append((rank[o.attempt_ref], o.observed_at))
        weights: dict[str, float] = {}
        per_attempt: dict[str, float] = {}
        for o in evidence:
            weight = o.reliability
            earlier = [
                seen
                for other_rank, seen in sightings[o.item_ref]
                if other_rank < rank[o.attempt_ref]
            ]
            if earlier:
                since = max(o.observed_at - max(earlier), timedelta(0))
                remembered = _fade(since, skill.half_life_days)
                weight *= 1.0 - (1.0 - params.repeat_item_factor) * remembered
            if o.kind == "stt_suspect":
                weight *= params.stt_suspect_factor
            weights[o.id] = weight
            per_attempt[o.attempt_ref] = per_attempt.get(o.attempt_ref, 0.0) + weight
        for o in evidence:
            total = per_attempt[o.attempt_ref]
            if total > params.attempt_weight_cap:
                weights[o.id] *= params.attempt_weight_cap / total

        success = failure = autonomous = 0.0  # weight above the prior, as of `last`
        last: datetime | None = None
        items: set[str] = set()
        contexts: set[str] = set()
        attempts: set[str] = set()
        # Whether the skill was mastered at some observation and has lost nothing since but time:
        # that, and only that, is a skill due for a recheck. Any result below `weak_mean`, of any
        # kind (a failure, a miss even with help, a possibly mistranscribed miss), is a loss.
        was_mastered = False
        applied = sorted((o for o in evidence if weights[o.id] > 0.0), key=lambda o: o.observed_at)
        for at, group in groupby(applied, key=lambda o: o.observed_at):
            if last is not None:
                fade = _fade(at - last, skill.half_life_days)
                success, failure, autonomous = success * fade, failure * fade, autonomous * fade
            for observation in group:
                assert observation.outcome is not None  # only silent kinds have none
                weight = weights[observation.id]
                success += weight * observation.outcome
                failure += weight * (1.0 - observation.outcome)
                if observation.kind != "assisted":
                    autonomous += weight
                items.add(observation.item_ref)
                contexts.add(observation.context)
                attempts.add(observation.attempt_ref)
                if observation.kind in FAILING_KINDS or observation.outcome < params.weak_mean:
                    was_mastered = False
            last = at
            if self._meets_mastery(
                skill, at, alpha0 + success, beta0 + failure, autonomous, contexts, attempts,
                critical_failures, tail,
            ):  # fmt: skip
                was_mastered = True

        if last is None:
            mean = beta_mean(alpha0, beta0)
            return MasteryEstimate(
                skill_id=skill.id,
                mean=mean,
                lower=min(beta_ppf(tail, alpha0, beta0), mean),
                upper=max(beta_ppf(1.0 - tail, alpha0, beta0), mean),
                effective_n=0.0,
                distinct_items=0,
                distinct_contexts=0,
                autonomous_n=0.0,
                last_observed_at=None,
                status="unknown",
                model_version=self.version,
            )

        elapsed = now - last
        fade = _fade(elapsed, skill.half_life_days)
        alpha, beta = alpha0 + success * fade, beta0 + failure * fade
        mean = beta_mean(alpha, beta)
        # Numeric safety: the interval always brackets the mean.
        lower = min(beta_ppf(tail, alpha, beta), mean)
        upper = max(beta_ppf(1.0 - tail, alpha, beta), mean)
        effective_n = (success + failure) * fade

        # Mastery is read on the numbers as they stand now, faded by time, so a "mastered" status
        # never sits next to a lower bound under the threshold, and a new success never takes it
        # away. A skill that was mastered and has since only faded is due for a recheck.
        if elapsed <= timedelta(days=skill.half_life_days) and self._meets_mastery(
            skill, now, alpha, beta, autonomous * fade, contexts, attempts, critical_failures, tail
        ):
            status: MasteryStatus = "mastered"
        elif was_mastered and not self._blocked(skill, now, critical_failures):
            status = "recheck"
        elif upper - lower > params.uncertain_width:
            status = "uncertain"
        elif mean < params.weak_mean and effective_n >= params.weak_min_n:
            status = "weak"
        else:
            status = "developing"
        return MasteryEstimate(
            skill_id=skill.id,
            mean=mean,
            lower=lower,
            upper=upper,
            effective_n=effective_n,
            distinct_items=len(items),
            distinct_contexts=len(contexts),
            autonomous_n=autonomous * fade,
            last_observed_at=last,
            status=status,
            model_version=self.version,
        )

    def _blocked(self, skill: Skill, at: datetime, critical_failures: list[datetime]) -> bool:
        window = timedelta(days=self.params.critical_window_days)
        return skill.critical and any(at - window <= f <= at for f in critical_failures)

    def _meets_mastery(
        self,
        skill: Skill,
        at: datetime,
        alpha: float,
        beta: float,
        autonomous: float,
        contexts: set[str],
        attempts: set[str],
        critical_failures: list[datetime],
        tail: float,
    ) -> bool:
        params = self.params
        return (
            len(contexts) >= params.min_contexts_for_mastery
            and len(attempts) >= params.min_attempts_for_mastery
            and autonomous >= params.min_autonomous_weight
            and not self._blocked(skill, at, critical_failures)
            and beta_ppf(tail, alpha, beta) >= params.mastered_lower
        )
