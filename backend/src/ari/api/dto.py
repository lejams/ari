from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ari.domain.clinical import Identifier
from ari.domain.models import CEFRLevel, InteractionMode, LearningMode


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateLearnerRequest(ApiModel):
    target_cefr: CEFRLevel = CEFRLevel.C1


class UpdateGoalRequest(ApiModel):
    target_exam: Literal["FSP"] = "FSP"
    target_cefr: CEFRLevel
    rubric_version: str = "fsp-anamnesis-v1"


class CreateSessionRequest(ApiModel):
    learner_id: str
    case_id: str
    case_version: str
    interaction_mode: InteractionMode = InteractionMode.GUIDED
    scenario_id: str | None = None
    scenario_version: str | None = None
    learning_mode: LearningMode | None = None
    request_id: Identifier | None = None


class EndSessionResponse(ApiModel):
    session: dict[str, object]


class ErrorResponse(ApiModel):
    detail: str = Field(min_length=1)
