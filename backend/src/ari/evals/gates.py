"""Promotion gates (`evals/gates.yaml`): thresholds per suite and critical outcomes.

A gate passes only when every threshold is met and the run has no NEW critical error: one
new critical error blocks whatever the averages. A metric that could not be measured (empty
denominator) fails its threshold: unknown is never taken as good.
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Threshold(_Strict):
    # Exact metric name, or a pattern ending in `*` applied to every measured metric it matches.
    metric: Annotated[str, Field(min_length=1)]
    min: float | None = None
    max: float | None = None
    source: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def one_bound(self) -> Threshold:
        if (self.min is None) == (self.max is None):
            raise ValueError(f"{self.metric}: give exactly one of min and max")
        return self

    def accepts(self, value: float) -> bool:
        if self.min is not None:
            return value >= self.min
        assert self.max is not None
        return value <= self.max

    @property
    def bound(self) -> str:
        return f">= {self.min}" if self.min is not None else f"<= {self.max}"


class SuiteGates(_Strict):
    thresholds: list[Threshold]
    critical_outcomes: list[str] = Field(default_factory=list)


class Gates(_Strict):
    version: Annotated[str, Field(min_length=1)]
    repetitions: Annotated[int, Field(ge=2)]
    suites: dict[str, SuiteGates]


class Check(_Strict):
    metric: str
    bound: str
    value: float | None
    status: Literal["passed", "failed", "not_measured"]


class GateResult(_Strict):
    gates_version: str
    status: Literal["passed", "failed"]
    checks: list[Check]
    new_critical_errors: list[str]  # "<item id>:<outcome>"
    baseline: str | None  # Config id of the baseline the critical errors were compared to.


def _check(metric: str, bound: str, value: float | None, status: str) -> Check:
    return Check.model_validate(
        {"metric": metric, "bound": bound, "value": value, "status": status}
    )


def load_gates(path: Path) -> tuple[Gates, str]:
    raw = path.read_bytes()
    return Gates.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


def critical_errors(
    outcomes_by_item: Mapping[str, Iterable[str]], critical: Iterable[str]
) -> list[str]:
    kinds = set(critical)
    return sorted(
        f"{item_id}:{outcome}"
        for item_id, outcomes in outcomes_by_item.items()
        for outcome in set(outcomes)
        if outcome in kinds
    )


def evaluate(
    gates: Gates,
    suite_ref: str,
    metrics: Mapping[str, float | None],
    critical: list[str],
    *,
    baseline_critical: list[str] | None = None,
    baseline_id: str | None = None,
) -> GateResult:
    suite = gates.suites.get(suite_ref)
    if suite is None:
        raise KeyError(f"{suite_ref} has no gate in gates.yaml")
    checks: list[Check] = []
    for threshold in suite.thresholds:
        if threshold.metric.endswith("*"):
            names = [
                name
                for name, value in sorted(metrics.items())
                if fnmatch.fnmatchcase(name, threshold.metric) and value is not None
            ]
            if not names:
                checks.append(_check(threshold.metric, threshold.bound, None, "not_measured"))
        else:
            names = [threshold.metric]
        for name in names:
            value = metrics.get(name)
            if value is None:
                checks.append(_check(name, threshold.bound, None, "not_measured"))
            else:
                status = "passed" if threshold.accepts(value) else "failed"
                checks.append(_check(name, threshold.bound, round(value, 6), status))
    new = sorted(set(critical) - set(baseline_critical or ()))
    passed = not new and all(check.status == "passed" for check in checks)
    return GateResult(
        gates_version=gates.version,
        status="passed" if passed else "failed",
        checks=checks,
        new_critical_errors=new,
        baseline=baseline_id,
    )
