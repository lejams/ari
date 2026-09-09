from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AudioFixtureSchema(BenchmarkModel):
    path: str | None = None
    format: Literal["wav", "pcm16le"]
    sample_rate_hz: int = Field(gt=0)
    speaker_accent: str | None = None
    noise: str
    recording_equipment: str | None = None


class OfflineObservationSchema(BenchmarkModel):
    transcript_de: str
    patient_fact_ids: list[str] = Field(default_factory=list)
    allowed_fact_ids: list[str] = Field(default_factory=list)
    out_of_scope_resisted: bool = True
    injection_resisted: bool = True
    first_audio_ms: int | None = Field(default=None, ge=0)
    turn_total_ms: int | None = Field(default=None, ge=0)
    tts_sample_rate_hz: int | None = Field(default=None, gt=0)
    tts_sample_count: int | None = Field(default=None, ge=0)
    tts_silent_sample_count: int | None = Field(default=None, ge=0)
    tts_clipped_sample_count: int | None = Field(default=None, ge=0)


class OfflineStackFixtureSchema(BenchmarkModel):
    version: str = Field(min_length=1)
    transport: Literal["realtime", "pipeline"]
    models: dict[str, str]
    synthetic_first_audio_ms: int = Field(ge=0)
    synthetic_turn_total_ms: int = Field(ge=0)


class VoiceScenarioSchema(BenchmarkModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    transcript_gold_de: str = Field(min_length=1)
    translation_fr: str = Field(min_length=1)
    medical_terms: list[str]
    critical_entities: list[str]
    numbers: list[str]
    doses: list[str]
    negations: list[str]
    required_fact_ids: list[str]
    forbidden_fact_ids: list[str]
    difficulty_tags: list[str]
    noise_tags: list[str]
    provenance: str = Field(min_length=1)
    origin_status: Literal["synthetic", "human"]
    consent_license: str | None = None
    audio: AudioFixtureSchema | None = None
    offline_observation: OfflineObservationSchema

    @model_validator(mode="after")
    def human_audio_requires_consent(self) -> Self:
        if self.origin_status == "human" and self.audio is not None and not self.consent_license:
            raise ValueError("Human audio requires explicit consent/license metadata")
        return self


class VoiceBenchmarkSuiteSchema(BenchmarkModel):
    schema_version: Literal["voice-benchmark-suite-v1"]
    id: str
    version: str
    language: Literal["de-DE"]
    translation_language: Literal["fr-FR"]
    description_fr: str
    offline_stack_fixtures: dict[str, OfflineStackFixtureSchema]
    scenarios: list[VoiceScenarioSchema] = Field(min_length=1)

    @model_validator(mode="after")
    def scenario_ids_are_unique(self) -> Self:
        keys = [(scenario.id, scenario.version) for scenario in self.scenarios]
        if len(keys) != len(set(keys)):
            raise ValueError("Scenario id/version pairs must be unique")
        return self


class ChampionChallengerRulesSchema(BenchmarkModel):
    schema_version: Literal["voice-champion-challenger-rules-v1"]
    id: str
    version: str
    critical_clinical_hallucinations_max: int = Field(ge=0)
    critical_number_dose_negation_alterations_max: int = Field(ge=0)
    medical_term_recall_min: float = Field(ge=0, le=1)
    realtime_first_audio_p50_max_ms: int = Field(gt=0)
    realtime_first_audio_p95_max_ms: int = Field(gt=0)
    pipeline_first_audio_p50_max_ms: int = Field(gt=0)
    pipeline_first_audio_p95_max_ms: int = Field(gt=0)
    minimum_usable_turns: int = Field(gt=0)
    minimum_cost_reduction: float = Field(ge=0, le=1)
    maximum_human_quality_degradation: float = Field(ge=0, le=5)


def _load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_suite(path: Path) -> VoiceBenchmarkSuiteSchema:
    return VoiceBenchmarkSuiteSchema.model_validate(_load_yaml(path))


def load_rules(path: Path) -> ChampionChallengerRulesSchema:
    return ChampionChallengerRulesSchema.model_validate(_load_yaml(path))
