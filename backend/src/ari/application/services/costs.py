from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ari.domain.models import CostStatus, ExecutionRecord, ExecutionStatus


@dataclass(frozen=True, slots=True)
class CostResult:
    component: str
    status: CostStatus
    amount_usd: float | None
    units: Mapping[str, float] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    unknown_reason: str | None = None
    pricing_version: str = "unknown"
    source_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CostStatus(self.status))
        if self.amount_usd is not None and (
            not math.isfinite(self.amount_usd) or self.amount_usd < 0
        ):
            raise ValueError("Cost must be finite and nonnegative")
        if self.status is CostStatus.UNKNOWN and self.amount_usd is not None:
            raise ValueError("Unknown cost cannot have an amount")
        if self.status is not CostStatus.UNKNOWN and self.amount_usd is None:
            raise ValueError("Known, estimated, or partial cost requires an amount")


def execution_cost_fields(result: CostResult) -> dict[str, object]:
    return {
        "estimated_cost_usd": result.amount_usd,
        "pricing_version": result.pricing_version,
        "cost_status": result.status,
        "cost_amount_usd": result.amount_usd,
        "cost_units": dict(result.units),
        "cost_assumptions": result.assumptions,
        "cost_unknown_reason": result.unknown_reason,
    }


def operation_component(operation: str) -> str:
    if operation.startswith("speech_to_text"):
        return "stt"
    if operation.startswith("text_to_speech"):
        return "tts"
    if operation.startswith("realtime_voice"):
        return "realtime"
    if operation in {"patient_response", "patient_simulation"}:
        return "patient_llm"
    if operation == "grounding_audit":
        return "grounding"
    if operation == "session_evaluation":
        return "evaluation"
    return "other"


def summarize_session_cost(
    session_id: str,
    voice_stack_id: str,
    executions: Sequence[ExecutionRecord],
) -> dict[str, object]:
    details: dict[str, dict[str, object]] = {}
    exact_total = 0.0
    estimated_total = 0.0
    partial_total = 0.0
    has_exact_amount = False
    has_estimated_amount = False
    has_partial_amount = False
    unknown_operations: list[str] = []
    included_operations: list[str] = []
    for component in (
        "stt",
        "patient_llm",
        "grounding",
        "tts",
        "realtime",
        "evaluation",
        "other",
    ):
        items = [item for item in executions if operation_component(item.operation) == component]
        if not items:
            continue
        component_exact = sum(
            item.cost_amount_usd
            for item in items
            if item.cost_status is CostStatus.EXACT and item.cost_amount_usd is not None
        )
        component_estimated = sum(
            item.cost_amount_usd
            for item in items
            if item.cost_status is CostStatus.ESTIMATED and item.cost_amount_usd is not None
        )
        component_partial = sum(
            item.cost_amount_usd
            for item in items
            if item.cost_status is CostStatus.PARTIAL and item.cost_amount_usd is not None
        )
        missing = [
            item.operation
            for item in items
            if item.status is ExecutionStatus.FAILED
            or item.cost_status in {CostStatus.PARTIAL, CostStatus.UNKNOWN}
        ]
        included = [
            item.operation
            for item in items
            if item.cost_status is not CostStatus.UNKNOWN and item.cost_amount_usd is not None
        ]
        exact_total += component_exact
        estimated_total += component_estimated
        partial_total += component_partial
        component_has_exact = any(
            item.cost_status is CostStatus.EXACT and item.cost_amount_usd is not None
            for item in items
        )
        component_has_estimated = any(
            item.cost_status is CostStatus.ESTIMATED and item.cost_amount_usd is not None
            for item in items
        )
        component_has_partial = any(
            item.cost_status is CostStatus.PARTIAL and item.cost_amount_usd is not None
            for item in items
        )
        has_exact_amount = has_exact_amount or component_has_exact
        has_estimated_amount = has_estimated_amount or component_has_estimated
        has_partial_amount = has_partial_amount or component_has_partial
        unknown_operations.extend(missing)
        included_operations.extend(included)
        details[component] = {
            "exact_cost_usd": component_exact if component_has_exact else None,
            "estimated_cost_usd": (
                component_estimated if component_has_estimated else None
            ),
            "partial_cost_usd": component_partial if component_has_partial else None,
            "estimated_or_partial_cost_usd": (
                component_estimated + component_partial
                if component_has_estimated or component_has_partial
                else None
            ),
            "operations": len(items),
            "included_operations": included,
            "missing_operations": missing,
        }
    has_known_amount = any(
        item.cost_status is not CostStatus.UNKNOWN and item.cost_amount_usd is not None
        for item in executions
    )
    has_estimated = any(item.cost_status is CostStatus.ESTIMATED for item in executions)
    has_partial = any(item.cost_status is CostStatus.PARTIAL for item in executions)
    if not executions:
        overall_status = CostStatus.UNKNOWN
    elif unknown_operations:
        overall_status = CostStatus.PARTIAL if has_known_amount else CostStatus.UNKNOWN
    elif has_partial:
        overall_status = CostStatus.PARTIAL
    elif has_estimated:
        overall_status = CostStatus.ESTIMATED
    else:
        overall_status = CostStatus.EXACT
    return {
        "session_id": session_id,
        "voice_stack_id": voice_stack_id,
        "status": overall_status.value,
        "exact_cost_usd": exact_total if has_exact_amount else None,
        "estimated_cost_usd": estimated_total if has_estimated_amount else None,
        "partial_cost_usd": partial_total if has_partial_amount else None,
        "estimated_or_partial_cost_usd": (
            estimated_total + partial_total
            if has_estimated_amount or has_partial_amount
            else None
        ),
        "total_is_exact": overall_status is CostStatus.EXACT,
        "included_operations": included_operations,
        "missing_operations": unknown_operations,
        "components": details,
        "pricing_versions": sorted({item.pricing_version for item in executions}),
    }
