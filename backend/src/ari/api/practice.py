"""Owned exercise API; every response uses an explicit non-answer-leaking projection."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ari.application.services.practice import content_summary, public_practice_run
from ari.application.services.progression import (
    practice_history,
    practice_progression,
    voice_history,
    voice_progression,
)
from ari.container import Container
from ari.domain.clinical import Identifier
from ari.domain.practice import PracticeMode


class PracticeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StartPractice(PracticeRequest):
    scenario_id: Identifier
    scenario_version: Identifier
    mode: PracticeMode
    request_id: Identifier


class AnswerPractice(PracticeRequest):
    question_id: Identifier
    event_id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=8000, pattern=r"\S")]


def practice_router(services: Container) -> APIRouter:
    router = APIRouter()
    practice = services.practice

    @router.get("/api/exercises")
    def exercises() -> dict[str, Any]:
        return {
            "items": [content_summary(c) for c in practice.catalog.list()],
            "demo_enabled": services.settings.enable_mvp_demos,
            "empty_message": "Aucun exercice approuvé disponible. Les brouillons sont exclus.",
        }

    @router.post("/api/practice/runs", status_code=201)
    def start(body: StartPractice, request: Request) -> dict[str, Any]:
        return public_practice_run(
            practice.start(
                request.state.learner_id,
                body.scenario_id,
                body.scenario_version,
                body.mode,
                body.request_id,
            )
        )

    @router.get("/api/practice/runs/{run_id}")
    def get(run_id: str, request: Request) -> dict[str, Any]:
        return public_practice_run(practice.repository.get(run_id, request.state.learner_id))

    @router.post("/api/practice/runs/{run_id}/answers")
    def answer(run_id: str, body: AnswerPractice, request: Request) -> dict[str, Any]:
        return public_practice_run(
            practice.answer(
                request.state.learner_id, run_id, body.question_id, body.text, body.event_id
            )
        )

    @router.post("/api/practice/runs/{run_id}/pause")
    def pause(run_id: str, request: Request) -> dict[str, Any]:
        return public_practice_run(
            practice.repository.set_paused(run_id, request.state.learner_id, True)
        )

    @router.post("/api/practice/runs/{run_id}/resume")
    def resume(run_id: str, request: Request) -> dict[str, Any]:
        return public_practice_run(
            practice.repository.set_paused(run_id, request.state.learner_id, False)
        )

    @router.post("/api/practice/runs/{run_id}/finish")
    def finish(run_id: str, request: Request) -> dict[str, Any]:
        return public_practice_run(practice.finish(request.state.learner_id, run_id))

    @router.get("/api/history")
    def history(request: Request) -> dict[str, Any]:
        owner = request.state.learner_id
        items = practice_history(practice.repository.list(owner)) + voice_history(
            services.repository.list_sessions(owner),
        )
        return {"items": sorted(items, key=lambda item: item["created_at"], reverse=True)}

    @router.get("/api/progression")
    def progression(request: Request) -> dict[str, Any]:
        owner = request.state.learner_id
        result = practice_progression(practice.repository.list(owner))
        voice = voice_progression(services.repository.list_sessions(owner), services.cases)
        result["groups"].extend(voice["groups"])
        result["excluded"] = voice["excluded"]
        result["state"] = "available" if result["groups"] else "no_data"
        return result

    return router
