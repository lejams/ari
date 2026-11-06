"""Language exercises API: the bank, drawn at the learner's working level, and the report button."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ari.application.services.exercise_practice import ReportReason
from ari.container import Container
from ari.domain.clinical import Identifier
from ari.domain.language_exercises import ExerciseCategory


class ExerciseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnswerExercise(ExerciseRequest):
    request_id: Identifier
    exercise_id: Identifier
    chosen_index: Annotated[int, Field(ge=0, le=9)]


class ReportExercise(ExerciseRequest):
    reason: ReportReason
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


def language_exercises_router(services: Container) -> APIRouter:
    router = APIRouter()
    practice = services.exercise_practice

    @router.get("/api/language-exercises")
    def overview(request: Request) -> dict[str, Any]:
        return practice.overview(request.state.learner_id)

    @router.get("/api/language-exercises/next")
    def next_exercise(category: ExerciseCategory, request: Request) -> dict[str, Any]:
        return practice.next(request.state.learner_id, category)

    @router.post("/api/language-exercises/answers")
    def answer(body: AnswerExercise, request: Request) -> dict[str, Any]:
        return practice.answer(
            request.state.learner_id,
            request_id=body.request_id,
            exercise_id=body.exercise_id,
            chosen_index=body.chosen_index,
        )

    @router.post("/api/language-exercises/{exercise_id}/report")
    def report(exercise_id: str, body: ReportExercise, request: Request) -> dict[str, Any]:
        return practice.report(
            request.state.learner_id, exercise_id, reason=body.reason, comment=body.comment
        )

    return router
