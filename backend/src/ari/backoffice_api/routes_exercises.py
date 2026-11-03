"""The bank of language exercises in the back-office: read by every account, withdrawn by the owner.

Read only apart from the withdrawal, which is final and leaves a trace in the exercise's reviews.
Nothing here writes content: an exercise enters the bank through `python -m ari.exercises_cli
import`, after the checks and a passing gate report (docs/LANGUAGE_EXERCISES.md).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import current_account, require_roles
from ari.backoffice_api.dto import WithdrawExerciseRequest
from ari.content.domain.accounts import AccountContext, Role
from ari.domain.language_exercises import (
    ExerciseCategory,
    ExerciseFormat,
    ExerciseLevel,
    ExerciseReview,
    ExerciseSkill,
    StoredExercise,
)

Owner = Annotated[AccountContext, Depends(require_roles(Role.OWNER))]
Anyone = Annotated[AccountContext, Depends(current_account)]
PREVIEW_LENGTH = 120


def _summary(stored: StoredExercise) -> dict[str, Any]:
    exercise = stored.exercise
    stem = exercise.stem
    return {
        "id": exercise.exercise_id,
        "level": exercise.level,
        "category": exercise.category,
        "skill": exercise.skill,
        "format": exercise.format,
        "status": stored.status,
        "setting": exercise.setting,
        "stem": stem if len(stem) <= PREVIEW_LENGTH else stem[: PREVIEW_LENGTH - 1] + "…",
        "batch_id": stored.batch_id,
        "created_at": stored.created_at.isoformat(),
        "withdrawn_reason": stored.withdrawn_reason,
    }


def _review(review: ExerciseReview) -> dict[str, Any]:
    return {
        "kind": review.reviewer_kind,
        "name": review.reviewer_name,
        "decision": review.decision,
        "payload": review.payload,
        "created_at": review.created_at.isoformat(),
    }


def exercises_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    bank = services.exercises

    @router.get("/api/exercises")
    def list_exercises(
        account: Anyone,
        status: Annotated[Literal["active", "withdrawn"] | None, Query()] = None,
        level: Annotated[ExerciseLevel | None, Query()] = None,
        category: Annotated[ExerciseCategory | None, Query()] = None,
        skill: Annotated[ExerciseSkill | None, Query()] = None,
        format: Annotated[ExerciseFormat | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        items, total = bank.search(
            status=status,
            level=level,
            category=category,
            skill=skill,
            format=format,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [_summary(stored) for stored in items],
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": bank.counts(),
        }

    @router.get("/api/exercises/{exercise_id}")
    def exercise(exercise_id: str, account: Anyone) -> dict[str, Any]:
        stored = bank.get(exercise_id)
        item = stored.exercise
        return {
            **_summary(stored),
            "stem": item.stem,
            "context_fr": item.context_fr,
            "instruction_fr": item.instruction_fr,
            "options": list(item.options),
            "answer_index": item.answer_index,
            "correct_sentence": item.correct_sentence(),
            "explanation_fr": item.explanation_fr,
            "provenance": stored.provenance,
            "reviews": [_review(review) for review in bank.reviews(exercise_id)],
        }

    @router.post("/api/exercises/{exercise_id}/withdraw")
    def withdraw(exercise_id: str, body: WithdrawExerciseRequest, account: Owner) -> dict[str, Any]:
        bank.withdraw(exercise_id, reason=body.reason, actor=account.display_name)
        return _summary(bank.get(exercise_id))

    return router
