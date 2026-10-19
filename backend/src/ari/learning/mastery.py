"""`mastery-beta-v1`: a Beta model of mastery per skill, with forgetting and honest uncertainty.

Each skill starts from a prior Beta(alpha0, beta0) (uniform by default). An observation with
outcome o and weight w moves the counts: alpha += w * o, beta += w * (1 - o). The weight is the
observation's reliability, reduced when the same item was already seen, when the evidence may
come from a transcription error, and capped per attempt so that one long session cannot
settle a skill on its own.

Between observations the evidence fades: the counts above the prior lose half their weight
every `half_life_days` of the skill, which is how a skill supposed mastered becomes uncertain
and then due for a recheck. The estimate is the mean of the Beta with a central credible
interval, and the status is read from the lower bound, so the model errs on the side of not
overstating what a learner can do.

Everything is recomputed from the list of observations: there is no incremental state to drift,
and replaying twice, in any input order, gives the same result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ari.learning.beta import beta_mean, beta_ppf
from ari.learning.model import (
    FAILING_KINDS,
    SILENT_KINDS,
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
    repeat_item_factor: float = 0.5  # weight kept when the item was already observed
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
        if self.prior_alpha <= 0 or self.prior_beta <= 0:
            raise ValueError("the prior needs positive counts")
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
        tail = (1.0 - params.interval_mass) / 2.0

        # Only what was known at `now`; of two assessments of the same evidence, the later one.
        latest: dict[tuple[str, str, str, str, str], Observation] = {}
        for observation in sorted(
            (o for o in observations if o.skill_id == skill.id and o.observed_at <= now),
            key=lambda o: (o.observed_at, o.id),
        ):
            latest[observation.replacement_key] = observation

        success = failure = autonomous = 0.0  # weight above the prior, as of `last`
        last: datetime | None = None
        spent: dict[str, float] = {}  # weight each attempt has added to this skill
        items: set[str] = set()
        contexts: set[str] = set()
        attempts: set[str] = set()
        critical_failures: list[datetime] = []

        for observation in sorted(latest.values(), key=lambda o: (o.observed_at, o.id)):
            if observation.kind in SILENT_KINDS:
                continue
            if observation.outcome is None:
                raise ValueError(f"observation {observation.id} has no outcome")
            weight = observation.reliability
            if observation.item_ref in items:
                weight *= params.repeat_item_factor
            if observation.kind == "stt_suspect":
                weight *= params.stt_suspect_factor
            weight = min(
                weight,
                max(params.attempt_weight_cap - spent.get(observation.attempt_ref, 0.0), 0.0),
            )
            if weight <= 0.0:
                continue
            if last is not None:
                fade = _fade(observation.observed_at - last, skill.half_life_days)
                success, failure, autonomous = success * fade, failure * fade, autonomous * fade
            spent[observation.attempt_ref] = spent.get(observation.attempt_ref, 0.0) + weight
            success += weight * observation.outcome
            failure += weight * (1.0 - observation.outcome)
            if observation.kind != "assisted":
                autonomous += weight
            items.add(observation.item_ref)
            contexts.add(observation.context)
            attempts.add(observation.attempt_ref)
            if observation.kind in FAILING_KINDS:
                critical_failures.append(observation.observed_at)
            last = observation.observed_at

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

        status = self._status(
            skill=skill,
            now=now,
            elapsed=elapsed,
            lower_at_last=beta_ppf(tail, alpha0 + success, beta0 + failure),
            autonomous_at_last=autonomous,
            contexts=len(contexts),
            attempts=len(attempts),
            critical_failures=critical_failures,
            width=upper - lower,
            mean=mean,
            effective_n=effective_n,
        )
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

    def _status(
        self,
        *,
        skill: Skill,
        now: datetime,
        elapsed: timedelta,
        lower_at_last: float,
        autonomous_at_last: float,
        contexts: int,
        attempts: int,
        critical_failures: list[datetime],
        width: float,
        mean: float,
        effective_n: float,
    ) -> MasteryStatus:
        params = self.params
        window = timedelta(days=params.critical_window_days)
        blocked = skill.critical and any(now - at <= window for at in critical_failures)
        # Mastery is judged on the evidence as it stood at the last observation; time then only
        # makes it stale. Judged on the faded numbers, a mastered skill would silently fall to
        # "developing" and a recheck would never be asked for.
        was_mastered = (
            lower_at_last >= params.mastered_lower
            and contexts >= params.min_contexts_for_mastery
            and attempts >= params.min_attempts_for_mastery
            and autonomous_at_last >= params.min_autonomous_weight
            and not blocked
        )
        if was_mastered:
            return "mastered" if elapsed <= timedelta(days=skill.half_life_days) else "recheck"
        if width > params.uncertain_width:
            return "uncertain"
        if mean < params.weak_mean and effective_n >= params.weak_min_n:
            return "weak"
        return "developing"
