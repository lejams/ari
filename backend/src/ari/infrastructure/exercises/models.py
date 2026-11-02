"""Which models author, solve and judge the exercises, read from a versioned configuration file.

`evals/configs/exercises/*.yaml` names the author's route and the checker's route (the model that
solves blind and the model that judges), and the version of the three prompts. The same file
drives the generation (`python -m ari.exercises_cli generate`) and the evaluation suite, so what
is measured is what makes the bank. The checker must not be the author's model: the
configuration is refused otherwise, and the service checks it again on the calls themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.exercise_schemas import RUBRIC_CRITERIA, BlindSolveSchema
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt, load_prompt
from ari.application.services.exercise_bank import OP_GENERATE
from ari.config import Settings
from ari.domain.errors import AriError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

PROMPT_ID = re.compile(r"^[a-z][a-z0-9-]*-v\d+$")
EXERCISE_PROMPTS = Path(__file__).resolve().parents[2] / "exercise_prompts"
TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_TOKENS = 16000


class ExerciseConfigError(AriError):
    pass


class ExerciseRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai", "anthropic"]
    model: Annotated[str, Field(min_length=1)]
    # Anthropic only. Pinned here, never read from the environment, so that what the gate
    # measures is what generates: the file is hashed into the report and into the batch.
    max_tokens: Annotated[int, Field(ge=1024)] = DEFAULT_MAX_TOKENS
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider, self.model)

    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class ExercisePrompts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author: Annotated[str, Field(pattern=PROMPT_ID.pattern)]
    solver: Annotated[str, Field(pattern=PROMPT_ID.pattern)]
    judge: Annotated[str, Field(pattern=PROMPT_ID.pattern)]


class ExerciseModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
    description: Annotated[str, Field(min_length=1)]
    provider: Literal["fake", "live"]
    routes: dict[Literal["author", "checker"], ExerciseRoute] = Field(default_factory=dict)
    prompts: ExercisePrompts

    @model_validator(mode="after")
    def routes_match_provider(self) -> ExerciseModelsConfig:
        if self.provider == "fake":
            if self.routes:
                raise ValueError("the fake provider takes no route")
        else:
            if set(self.routes) != {"author", "checker"}:
                raise ValueError("a live configuration routes the author and the checker")
            if self.routes["author"].identity == self.routes["checker"].identity:
                raise ValueError(
                    "the checker must be another model than the author "
                    f"({self.routes['author'].label()} on both)"
                )
        return self

    @property
    def is_live(self) -> bool:
        return self.provider == "live"

    @property
    def author_identity(self) -> tuple[str, str]:
        """(provider, model) of the author as the calls will record it."""
        return self.routes["author"].identity if self.is_live else ("fake", FAKE_AUTHOR)

    def route_labels(self) -> dict[str, str]:
        return {role: route.label() for role, route in self.routes.items()}


def load_config(path: Path) -> tuple[ExerciseModelsConfig, str]:
    """The configuration and the SHA-256 of its file."""
    raw = path.read_bytes()
    try:
        return ExerciseModelsConfig.model_validate(yaml.safe_load(raw)), hashlib.sha256(
            raw
        ).hexdigest()
    except (ValidationError, yaml.YAMLError) as exc:
        raise ExerciseConfigError(f"{path}: {exc}") from exc


def load_prompts(
    config: ExerciseModelsConfig, directory: Path = EXERCISE_PROMPTS
) -> dict[str, VersionedPrompt]:
    """`exercise-author-v1` is `exercise_author_v1.txt`, the convention of the other prompts."""
    prompts: dict[str, VersionedPrompt] = {}
    for role, version in config.prompts.model_dump().items():
        path = directory / f"{version.replace('-', '_')}.txt"
        if not path.is_file():
            raise ExerciseConfigError(f"{config.id}: prompt file {path.name} not found")
        prompts[role] = load_prompt(path, version)
    return prompts


class _RoutedProvider:
    """The author's route for the generation, the checker's for solving and judging."""

    def __init__(self, config: ExerciseModelsConfig, settings: Settings) -> None:
        from ari.infrastructure.providers.anthropic import AnthropicLLMProvider
        from ari.infrastructure.providers.openai.llm import OpenAILLMProvider

        self._routes = config.routes
        providers = {route.provider for route in config.routes.values()}
        keys = {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key}
        missing = [f"ARI_{name.upper()}_API_KEY" for name in sorted(providers) if not keys[name]]
        if missing:
            raise ExerciseConfigError(
                f"{', '.join(missing)} required for the routes "
                f"({', '.join(f'{r}={x.label()}' for r, x in sorted(config.routes.items()))})"
            )
        # One backend per role, so that each route keeps its own settings (token limit, effort)
        # even when both roles use the same vendor.
        self._backends: dict[str, OpenAILLMProvider | AnthropicLLMProvider] = {}
        for role, route in config.routes.items():
            if route.provider == "openai":
                self._backends[role] = OpenAILLMProvider(
                    keys["openai"] or "",
                    patient_model=route.model,
                    evaluation_model=route.model,
                    patient_timeout_seconds=TIMEOUT_SECONDS,
                    evaluation_timeout_seconds=TIMEOUT_SECONDS,
                )
            else:
                self._backends[role] = AnthropicLLMProvider(
                    keys["anthropic"] or "", max_tokens=route.max_tokens, effort=route.effort
                )

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        role: Literal["author", "checker"] = (
            "author" if request.context.operation == OP_GENERATE else "checker"
        )
        route = self._routes[role]
        return await self._backends[role].generate(
            request, response_model, model=route.model, timeout_seconds=TIMEOUT_SECONDS
        )


FAKE_AUTHOR = "fake-exercise-author-v1"
FAKE_CHECKER = "fake-exercise-checker-v1"
_FAKE_SENTENCES = (
    ("Der Arzt erklärt ___ Patienten die Untersuchung.", "dem", ["den", "der"], "anamnèse"),
    ("Ich nehme ___ Tablette nach dem Essen.", "eine", ["einen", "einem"], "prescription"),
    ("Die Schwester bringt ___ Patientin das Frühstück.", "der", ["die", "den"], "hospitalisation"),
    ("Er wartet ___ den Befund seit einer Woche.", "auf", ["für", "um"], "attente"),
    ("Sie hat gestern lange im Wartezimmer ___.", "gesessen", ["gesitzt", "sitzte"], "rendez-vous"),
    ("Wir besprechen ___ Ergebnisse morgen früh.", "die", ["der", "dem"], "transmission"),
)


class FakeExerciseProvider:
    """Plumbing only: the key sorts first among the options, which is all the fake solver reads.

    It measures nothing about German. The author gives keys that sort before their distractors,
    the solver picks the option that sorts first, the judge approves everything; a configuration
    using it can run the chain end to end without a key and never reach the bank.
    """

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        started = time.perf_counter()
        payload = json.loads(request.messages[-1]["content"])
        operation = request.context.operation
        if operation == OP_GENERATE:
            model, value = FAKE_AUTHOR, self._author(payload)
        elif response_model is BlindSolveSchema:
            model, value = FAKE_CHECKER, self._solve(payload)
        else:
            model, value = FAKE_CHECKER, self._judge()
        parsed = response_model.model_validate_json(json.dumps(value))
        context = request.context
        return ProviderResult(
            value=parsed,
            execution=ExecutionRecord(
                id=new_id(),
                session_id=context.session_id,
                turn_id=context.turn_id,
                operation=operation,
                provider="fake",
                model=model,
                model_returned=model,
                status=ExecutionStatus.SUCCEEDED,
                prompt_version=context.prompt_version,
                prompt_hash=context.prompt_hash,
                case_version=context.case_version,
                case_hash=context.case_hash,
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage={},
            ),
        )

    @staticmethod
    def _author(payload: dict[str, object]) -> dict[str, object]:
        count = int(str(payload["count"]))
        is_cloze = payload["format"] == "cloze"
        drafts: list[dict[str, object]] = []
        for number in range(count):
            stem, key, distractors, setting = _FAKE_SENTENCES[number % len(_FAKE_SENTENCES)]
            if not is_cloze:
                right, first, second = (stem.replace("___", w) for w in (key, *distractors[:2]))
                # The key sorts before its distractors, the only thing the fake solver reads.
                stem, key, distractors = first, right, ["~" + first, "~" + second]
            drafts.append(
                {
                    "stem": stem,
                    "key": key,
                    "distractors": distractors,
                    "explanation_fr": "Exemple de la chaîne simulée, sans valeur linguistique.",
                    "context_fr": (
                        "Un médecin parle à un patient." if payload["format"] != "cloze" else ""
                    ),
                    "setting": setting,
                }
            )
        return {"exercises": drafts}

    @staticmethod
    def _solve(payload: dict[str, object]) -> dict[str, object]:
        options = [(str(o["text"]), int(str(o["index"]))) for o in payload["options"]]  # type: ignore[attr-defined]
        index = min(options)[1]
        return {
            "chosen_index": index,
            "acceptable_indices": [index],
            "medically_plausible": True,
            "comment": "simulated",
        }

    @staticmethod
    def _judge() -> dict[str, object]:
        return {
            "verdicts": [
                {"criterion": c, "passed": True, "reason": "simulated"} for c in RUBRIC_CRITERIA
            ]
        }


def build_provider(config: ExerciseModelsConfig, *, live: bool) -> LLMProvider:
    """The provider of the configuration. Real models are called only with `live=True`."""
    if not config.is_live:
        return FakeExerciseProvider()
    if not live:
        raise ExerciseConfigError(
            f"{config.id} appelle de vrais modèles (facturés) : relancer avec --live pour le "
            "confirmer."
        )
    return _RoutedProvider(config, Settings())


__all__ = [
    "ExerciseConfigError",
    "ExerciseModelsConfig",
    "FakeExerciseProvider",
    "build_provider",
    "load_config",
    "load_prompts",
]
