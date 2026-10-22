"""A configuration under test: the LLM route of each task, the prompts, the patient engine.

A route is exactly a production setting, and the live provider is built by the production
routing code (`llm_provider_from_settings`), so what is measured is what would run. Adopting a
configuration, or rolling it back, is setting environment variables:
`routes.<task>.provider` is `ARI_<TASK>_PROVIDER`, `routes.<task>.model` is `ARI_<TASK>_MODEL`
(tasks: patient, evaluation, fact_attribution).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt, load_prompt
from ari.application.services.costing import PricingTable

Role = Literal["patient", "evaluation"]
Task = Literal["patient", "evaluation", "fact_attribution"]
PROMPT_ID = re.compile(r"^[a-z][a-z0-9-]*-v\d+$")


class ConfigError(Exception):
    pass


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai", "anthropic"]
    model: Annotated[str, Field(min_length=1)]


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
    description: Annotated[str, Field(min_length=1)]
    provider: Literal["fake", "live"]
    # Route per task; the fake provider has a single deterministic model and takes none.
    # Without a fact_attribution route, the fact audit follows the patient, as in production.
    routes: dict[Task, Route] = Field(default_factory=dict)
    prompts: dict[Role, Annotated[str, Field(pattern=PROMPT_ID.pattern)]]
    # Which patient answers in patient-leakage: the training simulator or the exam chain.
    patient_engine: Literal["training", "exam"] = "training"
    feedback_language: str = "fr-FR"

    @model_validator(mode="after")
    def routes_match_provider(self) -> EvalConfig:
        if self.provider == "fake" and self.routes:
            raise ValueError("the fake provider takes no route")
        if self.provider == "live" and not {"patient", "evaluation"} <= set(self.routes):
            raise ValueError("a live configuration routes the patient and the evaluation")
        return self

    @property
    def is_live(self) -> bool:
        return self.provider == "live"

    def route_labels(self) -> dict[str, str]:
        return {task: f"{r.provider}:{r.model}" for task, r in self.routes.items()}


def load_config(path: Path) -> tuple[EvalConfig, str]:
    """The configuration and the SHA-256 of its file."""
    raw = path.read_bytes()
    try:
        config = EvalConfig.model_validate(yaml.safe_load(raw))
    except Exception as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return config, hashlib.sha256(raw).hexdigest()


def load_prompts(
    config: EvalConfig, roles: tuple[str, ...], directory: Path
) -> dict[str, VersionedPrompt]:
    """`patient-v3` is `patient_v3.txt`, the convention of `backend/src/ari/prompts/`."""
    prompts: dict[str, VersionedPrompt] = {}
    for role in roles:
        version = {str(k): v for k, v in config.prompts.items()}.get(role)
        if version is None:
            raise ConfigError(f"{config.id}: no prompt for the {role} role")
        path = directory / f"{version.replace('-', '_')}.txt"
        if not path.is_file():
            raise ConfigError(f"{config.id}: prompt file {path.name} not found")
        prompts[role] = load_prompt(path, version)
    return prompts


def build_provider(config: EvalConfig, *, live: bool, pricing: PricingTable) -> LLMProvider:
    """The provider of the configuration. Real models are called only with `live=True`."""
    from ari.infrastructure.providers.fake import FakeLLMProvider

    if not config.is_live:
        return FakeLLMProvider()
    if not live:
        raise ConfigError(
            f"{config.id} appelle de vrais modèles (facturés) : relancer avec --live pour le "
            "confirmer."
        )
    from ari.config import Settings
    from ari.infrastructure.providers.llm_routing import (
        LEARNER_TASKS,
        llm_provider_from_settings,
    )

    update: dict[str, object] = {"fact_attribution_provider": None, "fact_attribution_model": None}
    for task, route in config.routes.items():
        update[f"{task}_provider"] = route.provider
        update[f"{task}_model"] = route.model
    settings = Settings().model_copy(update=update)
    try:
        return llm_provider_from_settings(settings, pricing, LEARNER_TASKS)
    except RuntimeError as exc:  # Missing API key or unpriced route.
        raise ConfigError(str(exc)) from exc
