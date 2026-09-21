"""Weekly programme API: the learner model and this week's slots, recomputed per request."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from ari.application.services.learner_model import LearnerModelService
from ari.application.services.program import plan_week, week_start
from ari.container import Container
from ari.domain.models import LearningMode, SessionStatus


def completed_since_week_start(
    services: Container, learner_id: str, now: datetime
) -> dict[str, list[datetime]]:
    start = datetime.combine(week_start(now.date()), datetime.min.time(), tzinfo=UTC)
    completed: dict[str, list[datetime]] = {}
    for session in services.repository.list_sessions(learner_id):
        if (
            session.status is SessionStatus.COMPLETED
            and session.ended_at
            and session.ended_at >= start
        ):
            kind = "voice_exam" if session.learning_mode is LearningMode.EXAM else "voice_training"
            completed.setdefault(kind, []).append(session.ended_at)
    for run in services.practice.repository.list(learner_id):
        if run.status == "completed" and run.ended_at and run.ended_at >= start:
            completed.setdefault(run.content.bundle.scenarios[0].phase, []).append(run.ended_at)
    for entry in services.lexicon.repository.list(learner_id):
        reviewed = entry.srs.last_reviewed_at
        if reviewed and reviewed >= start:
            # One review day counts as one lexicon slot done, whatever the number of cards.
            kind = "lexicon_maintenance" if entry.state.value == "mastered" else "lexicon_review"
            completed.setdefault(kind, []).append(reviewed)
    for kind in ("lexicon_review", "lexicon_maintenance"):
        if kind in completed:
            by_day = {t.date(): t for t in completed[kind]}
            completed[kind] = sorted(by_day.values())
    for attempt in services.placement.attempts.list(learner_id):
        if attempt.status == "completed" and attempt.ended_at and attempt.ended_at >= start:
            completed.setdefault("placement", []).append(attempt.ended_at)
    return completed


def program_router(services: Container) -> APIRouter:
    router = APIRouter()
    models = LearnerModelService(services.repository, services.lexicon)

    @router.get("/api/program")
    def program(request: Request) -> dict[str, Any]:
        learner_id = request.state.learner_id
        now = datetime.now(UTC)
        runs = services.practice.repository.list(learner_id)
        model = models.compute(learner_id, runs, now)
        week = plan_week(
            model,
            services.cases.list(),
            services.practice.catalog.list(),
            now.date(),
            completed=completed_since_week_start(services, learner_id, now),
        )
        return {"model": model.as_public(), "week": week}

    return router
