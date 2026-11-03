"""Structured outputs of the exercise bank: what the author, the blind solver and the judge return.

The rubric of the judge is a versioned asset: its criteria are listed here (the code requires
exactly one verdict per criterion) and described in `exercise_prompts/exercise_judge_v1.txt`. A
change to the criteria or to their wording is a new rubric version.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ari.application.schemas import StrictModel

RUBRIC_VERSION = "exercise-rubric-v1"
RUBRIC_CRITERIA: tuple[str, ...] = (
    "key_correct",
    "distractors_wrong",
    "targets_category",
    "level_fit",
    "explanation_correct",
    "natural_register",
    "medical_plausible",
    "self_contained",
)
CriterionId = Literal[
    "key_correct",
    "distractors_wrong",
    "targets_category",
    "level_fit",
    "explanation_correct",
    "natural_register",
    "medical_plausible",
    "self_contained",
]


class ExerciseDraftSchema(StrictModel):
    """One proposal of the author; the code builds the options and checks the rest."""

    stem: Annotated[str, Field(min_length=1, max_length=400)]
    key: Annotated[str, Field(min_length=1, max_length=400)]
    distractors: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        min_length=2, max_length=3
    )
    explanation_fr: Annotated[str, Field(min_length=1, max_length=700)]
    context_fr: Annotated[str, Field(max_length=300)]  # Empty when there is none.
    setting: Annotated[str, Field(min_length=1, max_length=60)]


class ExerciseBatchSchema(StrictModel):
    exercises: list[ExerciseDraftSchema] = Field(min_length=1, max_length=10)


class BlindSolveSchema(StrictModel):
    chosen_index: Annotated[int, Field(ge=0, le=3)]
    # Every option the solver would accept, the chosen one included.
    acceptable_indices: list[Annotated[int, Field(ge=0, le=3)]] = Field(min_length=1, max_length=4)
    medically_plausible: bool
    comment: Annotated[str, Field(max_length=300)]

    @model_validator(mode="after")
    def chosen_is_acceptable(self) -> Self:
        if self.chosen_index not in self.acceptable_indices:
            raise ValueError("the chosen option must be among the acceptable ones")
        if len(set(self.acceptable_indices)) != len(self.acceptable_indices):
            raise ValueError("acceptable_indices must be unique")
        return self


class CriterionVerdictSchema(StrictModel):
    criterion: CriterionId
    passed: bool
    reason: Annotated[str, Field(min_length=1, max_length=400)]


class JudgeSchema(StrictModel):
    verdicts: list[CriterionVerdictSchema] = Field(
        min_length=len(RUBRIC_CRITERIA), max_length=len(RUBRIC_CRITERIA)
    )

    @model_validator(mode="after")
    def one_verdict_per_criterion(self) -> Self:
        if sorted(v.criterion for v in self.verdicts) != sorted(RUBRIC_CRITERIA):
            raise ValueError("exactly one verdict per rubric criterion is required")
        return self

    @property
    def approved(self) -> bool:
        """The code decides: approved only when every criterion passed."""
        return all(v.passed for v in self.verdicts)
