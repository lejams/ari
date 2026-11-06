"""Every exercise in one list, read by every account: language exercises and medical scenarios.

Two kinds, two mechanisms, one view. A language exercise (cloze, correct_sentence,
register_choice) is model-written and model-checked, and lives in the exercise bank; the owner can
withdraw it here, which is final and leaves a trace in its reviews. A medical scenario
(arzt_patient, arzt_arzt, fachbegriffe) comes from a frozen gold protocol and is reviewed by two
humans in the registry; the list only points to its registry page, where it is reviewed, published
and withdrawn. Nothing here writes content: a language exercise enters the bank through
`python -m ari.exercises_cli import`, after a passing gate report (docs/LANGUAGE_EXERCISES.md).

A learner's report holds an exercise back from every learner ("reported"). The owner settles it
here: withdraw it, or put it back in circulation with a note; both are reviews in its history.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import current_account, require_roles
from ari.backoffice_api.dto import ReinstateExerciseRequest, WithdrawExerciseRequest
from ari.content.domain.accounts import AccountContext, Role
from ari.domain.errors import InvalidStateError
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
LanguageType = ExerciseFormat
MedicalType = Literal["arzt_patient", "arzt_arzt", "fachbegriffe", "arztbrief"]
ExerciseType = Literal[
    "cloze",
    "correct_sentence",
    "register_choice",
    "arzt_patient",
    "arzt_arzt",
    "fachbegriffe",
    "arztbrief",
]
MEDICAL_TYPES: tuple[str, ...] = ("arzt_patient", "arzt_arzt", "fachbegriffe", "arztbrief")
# One vocabulary for both kinds: a published scenario is "active", as an exercise in the bank is.
MEDICAL_STATUS = {"published": "active"}


def _cut(text: str) -> str:
    return text if len(text) <= PREVIEW_LENGTH else text[: PREVIEW_LENGTH - 1] + "…"


def _summary(stored: StoredExercise, held: frozenset[str] = frozenset()) -> dict[str, Any]:
    exercise = stored.exercise
    return {
        "kind": "language",
        "id": exercise.exercise_id,
        "version": None,
        "type": exercise.format,
        "level": exercise.level,
        "category": exercise.category,
        "skill": exercise.skill,
        "status": stored.status,
        "setting": exercise.setting,
        "stem": _cut(exercise.stem),
        "batch_id": stored.batch_id,
        "created_at": stored.created_at.isoformat(),
        "withdrawn_reason": stored.withdrawn_reason,
        "reported": exercise.exercise_id in held,
    }


def _medical_status(scenario: dict[str, Any]) -> str:
    status = str(scenario["status"])
    return MEDICAL_STATUS.get(status, status)


def _medical_summary(scenario: dict[str, Any]) -> dict[str, Any]:
    """A registry scenario seen as an exercise: it has a type and a status, not a level or skill."""
    return {
        "kind": "medical",
        "id": scenario["id"],
        "version": scenario["version"],
        "type": scenario["phase"],
        "level": None,
        "category": None,
        "skill": None,
        "status": _medical_status(scenario),
        "setting": scenario.get("land"),
        "stem": _cut(str(scenario.get("title") or scenario["case_id"])),
        "batch_id": None,
        "created_at": None,
        "withdrawn_reason": None,
        "reported": False,
    }


def _review(review: ExerciseReview) -> dict[str, Any]:
    if review.reviewer_kind == "learner_report":
        # The reason and the comment are what the owner needs; who reported stays out of view.
        return {
            "kind": review.reviewer_kind,
            "name": "Apprenant",
            "decision": review.decision,
            "payload": {k: v for k, v in review.payload.items() if k != "learner_id"},
            "created_at": review.created_at.isoformat(),
        }
    return {
        "kind": review.reviewer_kind,
        "name": review.reviewer_name,
        "decision": review.decision,
        "payload": review.payload,
        "created_at": review.created_at.isoformat(),
    }


def _counts(
    language: dict[str, dict[str, int]], scenarios: list[dict[str, Any]], reported: int
) -> dict[str, Any]:
    """How many exercises each filter value holds, for both kinds together."""
    types = dict(language.get("format", {}))
    statuses = dict(language.get("status", {}))
    if reported:
        statuses["reported"] = reported  # Counted within "active" too: still in the bank.
    for scenario in scenarios:
        types[scenario["phase"]] = types.get(scenario["phase"], 0) + 1
        status = _medical_status(scenario)
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "type": types,
        "status": statuses,
        "level": language.get("level", {}),
        "category": language.get("category", {}),
        "skill": language.get("skill", {}),
    }


def exercises_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    bank = services.exercises

    def medical(
        *,
        status: str | None,
        kind: str | None,
        language_filters: bool,
    ) -> list[dict[str, Any]]:
        """The registry scenarios matching the filters. Level, category and skill belong to language
        exercises only, so setting any of them leaves the medical scenarios out."""
        if language_filters or kind in {"cloze", "correct_sentence", "register_choice"}:
            return []
        items = [_medical_summary(s) for s in services.registry.list_scenarios()]
        return [
            item
            for item in items
            if (status is None or item["status"] == status)
            and (kind is None or item["type"] == kind)
        ]

    @router.get("/api/exercises")
    def list_exercises(
        account: Anyone,
        status: Annotated[
            Literal["active", "reported", "draft_unvalidated", "withdrawn"] | None, Query()
        ] = None,
        level: Annotated[ExerciseLevel | None, Query()] = None,
        category: Annotated[ExerciseCategory | None, Query()] = None,
        skill: Annotated[ExerciseSkill | None, Query()] = None,
        type: Annotated[ExerciseType | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Medical scenarios first (there are few), then language exercises, newest first."""
        scenarios = medical(
            status=status,
            kind=type,
            language_filters=any(value is not None for value in (level, category, skill)),
        )
        held = bank.held_for_review()
        language_type = None if type in MEDICAL_TYPES else type
        # "reported" is not a column: an active exercise held back by a learner's report.
        language_status = (
            None if status == "draft_unvalidated" else "active" if status == "reported" else status
        )
        if type in MEDICAL_TYPES or status == "draft_unvalidated":
            language_items: tuple[StoredExercise, ...] = ()
            language_total = 0
        else:
            language_items, language_total = bank.search(
                status=language_status,
                level=level,
                category=category,
                skill=skill,
                format=language_type,
                ids=held if status == "reported" else None,
                limit=max(0, limit - max(0, len(scenarios) - offset)),
                offset=max(0, offset - len(scenarios)),
            )
        page = scenarios[offset : offset + limit]
        return {
            "items": [*page, *(_summary(stored, held) for stored in language_items)],
            "total": len(scenarios) + language_total,
            "limit": limit,
            "offset": offset,
            "counts": _counts(bank.counts(), services.registry.list_scenarios(), len(held)),
        }

    @router.get("/api/exercises/{exercise_id}")
    def exercise(exercise_id: str, account: Anyone) -> dict[str, Any]:
        stored = bank.get(exercise_id)
        item = stored.exercise
        return {
            **_summary(stored, bank.held_for_review()),
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
        return _summary(bank.get(exercise_id), bank.held_for_review())

    @router.post("/api/exercises/{exercise_id}/reinstate")
    def reinstate(
        exercise_id: str, body: ReinstateExerciseRequest, account: Owner
    ) -> dict[str, Any]:
        """The owner read the reported exercise and finds it right: learners get it again."""
        if exercise_id not in bank.held_for_review():
            raise InvalidStateError("Cet exercice n'est pas retenu par un signalement")
        bank.add_review(
            exercise_id,
            ExerciseReview(
                "human", account.display_name, "approve", {"reinstated": body.note.strip()}
            ),
        )
        return _summary(bank.get(exercise_id), bank.held_for_review())

    return router
