"""Which provider and model answer each kind of structured-generation call.

ARI's calls fall into four tasks: the patient (training turns and the placement oral), the
session evaluation, the exam fact audit, and the content pipeline. Each task has its own route
(provider, model, timeout), set in configuration, so a task can move to another provider or
model without the application noticing: the port is still `LLMProvider.generate_structured`.

The defaults reproduce the routing that existed before this module: OpenAI everywhere, the fact
audit on the patient model.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.services.costing import PricingTable
from ari.config import Settings

T = TypeVar("T", bound=BaseModel)

LLMTask = Literal["patient", "evaluation", "fact_attribution", "content"]
ProviderName = Literal["openai", "anthropic"]

# Operations of the content pipeline run on the content route with its long timeout.
CONTENT_OPERATIONS = frozenset({"protocol_segmentation", "protocol_extraction", "bundle_draft"})
LEARNER_TASKS: tuple[LLMTask, ...] = ("patient", "evaluation", "fact_attribution")
CONTENT_TASKS: tuple[LLMTask, ...] = ("content",)


def task_of(operation: str) -> LLMTask:
    if operation == "session_evaluation":
        return "evaluation"
    if operation == "fact_attribution":
        return "fact_attribution"
    if operation in CONTENT_OPERATIONS:
        return "content"
    return "patient"  # patient_simulation, placement_speaking


@dataclass(frozen=True, slots=True)
class LLMRoute:
    provider: ProviderName
    model: str
    timeout_seconds: float


class ModelBackend(Protocol):
    """One provider's structured generation, for a model chosen by the route."""

    async def generate(
        self, request: LLMRequest, response_model: type[T], *, model: str, timeout_seconds: float
    ) -> ProviderResult[T]: ...


class RoutedLLMProvider:
    def __init__(
        self, routes: Mapping[LLMTask, LLMRoute], backends: Mapping[str, ModelBackend]
    ) -> None:
        missing = sorted({r.provider for r in routes.values()} - set(backends))
        if missing:
            raise ValueError(f"no backend for provider(s) {missing}")
        self._routes = dict(routes)
        self._backends = dict(backends)

    def route(self, operation: str) -> LLMRoute:
        task = task_of(operation)
        if task not in self._routes:
            raise ValueError(f"operation {operation!r} ({task}) is not routed in this process")
        return self._routes[task]

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        route = self.route(request.context.operation)
        return await self._backends[route.provider].generate(
            request, response_model, model=route.model, timeout_seconds=route.timeout_seconds
        )


def routes_from_settings(settings: Settings) -> dict[LLMTask, LLMRoute]:
    patient = LLMRoute(
        settings.patient_provider, settings.patient_model, settings.patient_timeout_seconds
    )
    return {
        "patient": patient,
        "evaluation": LLMRoute(
            settings.evaluation_provider,
            settings.evaluation_model,
            settings.evaluation_timeout_seconds,
        ),
        # Unset, the fact audit follows the patient route, as it always has.
        "fact_attribution": LLMRoute(
            settings.fact_attribution_provider or patient.provider,
            settings.fact_attribution_model or patient.model,
            patient.timeout_seconds,
        ),
        "content": LLMRoute(
            settings.content_provider, settings.content_model, settings.content_timeout_seconds
        ),
    }


def llm_provider_from_settings(
    settings: Settings, pricing: PricingTable, tasks: Collection[LLMTask]
) -> RoutedLLMProvider:
    """The routed provider for the tasks this process runs (ARI_PROVIDER_MODE=openai).

    Refuses to start when a route needs a key that is not set, or when a non-OpenAI route
    names a model the price list does not know: its cost would otherwise stay unknown on every
    call. OpenAI routes keep their earlier behaviour (an unpriced model is recorded as unknown).
    """
    routes = {
        task: route for task, route in routes_from_settings(settings).items() if task in tasks
    }
    providers = {route.provider for route in routes.values()}
    keys = {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key}
    missing = [f"ARI_{name.upper()}_API_KEY" for name in sorted(providers) if not keys[name]]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} required in .env for the configured LLM routes "
            f"({', '.join(f'{t}={r.provider}:{r.model}' for t, r in sorted(routes.items()))})"
        )
    unpriced = sorted(
        f"{task}={route.provider}:{route.model}"
        for task, route in routes.items()
        if route.provider != "openai" and (route.provider, route.model) not in pricing.models
    )
    if unpriced:
        raise RuntimeError(
            f"no price for {', '.join(unpriced)} in {pricing.version}: set ARI_PRICING_PATH to a "
            "price list that has it (backend/src/ari/pricing/pricing-v2.yaml or later)"
        )
    backends: dict[str, ModelBackend] = {}
    if "openai" in providers:
        from ari.infrastructure.providers.openai.llm import OpenAILLMProvider

        assert settings.openai_api_key
        backends["openai"] = OpenAILLMProvider(
            settings.openai_api_key,
            patient_model=settings.patient_model,
            evaluation_model=settings.evaluation_model,
            patient_timeout_seconds=settings.patient_timeout_seconds,
            evaluation_timeout_seconds=settings.evaluation_timeout_seconds,
            content_model=settings.content_model,
            content_timeout_seconds=settings.content_timeout_seconds,
        )
    if "anthropic" in providers:
        from ari.infrastructure.providers.anthropic import AnthropicLLMProvider

        assert settings.anthropic_api_key
        backends["anthropic"] = AnthropicLLMProvider(
            settings.anthropic_api_key,
            max_tokens=settings.anthropic_max_tokens,
            effort=settings.anthropic_effort,
        )
    return RoutedLLMProvider(routes, backends)
