from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ari.domain.clinical import Identifier
from ari.domain.geography import Land
from ari.domain.models import CEFRLevel, LearnerDetails, LearningMode


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


TargetLevel = Literal["B2", "C1"]  # The working goal inside ARI stays B2 or C1.


class UpdateProfileRequest(ApiModel):
    """Declared facts about the learner. Only the fields sent are changed."""

    declared_level: CEFRLevel | None = None
    level_source: Literal["self", "certificate"] = "self"
    certificate_issuer: Literal["goethe", "telc", "osd", "testdaf", "dsh", "other"] | None = None
    certificate_level: CEFRLevel | None = None
    certificate_date: date | None = None
    exam_date: date | None = None
    minutes_per_day: Annotated[int, Field(ge=5, le=240)] = 30
    land: Land | None = None
    situation: Literal["doctor", "student"] | None = None
    specialty: Annotated[str, Field(max_length=120)] | None = None
    maintenance_cadence_days: Literal[7, 30] = 30

    def apply(self, current: LearnerDetails) -> LearnerDetails:
        changes = self.model_dump(exclude_unset=True)
        # A declared certificate fixes the declared level: one value, not two that disagree.
        if changes.get("level_source") == "certificate" and changes.get("certificate_level"):
            changes["declared_level"] = changes["certificate_level"]
        # Estimated level and its provenance come only from a placement attempt.
        return LearnerDetails(
            **{
                **{
                    name: getattr(current, name)
                    for name in LearnerDetails.__dataclass_fields__  # type: ignore[attr-defined]
                },
                **changes,
            }
        )


class CreateLearnerRequest(ApiModel):
    target_cefr: TargetLevel = "C1"
    details: UpdateProfileRequest | None = None


class UpdateGoalRequest(ApiModel):
    target_exam: Literal["FSP"] = "FSP"
    target_cefr: TargetLevel
    rubric_version: str = "fsp-anamnesis-v1"


class CreateSessionRequest(ApiModel):
    learner_id: str
    case_id: str
    case_version: str
    scenario_id: str | None = None
    scenario_version: str | None = None
    learning_mode: LearningMode | None = None
    request_id: Identifier | None = None


class EndSessionResponse(ApiModel):
    session: dict[str, object]


class ErrorResponse(ApiModel):
    detail: str = Field(min_length=1)
