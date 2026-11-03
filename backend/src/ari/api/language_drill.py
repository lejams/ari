"""Remediation drill API: "Votre phrase", the learner's own errors replayed."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, StringConstraints

from ari.container import Container
from ari.domain.clinical import Identifier


class DrillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StartDrill(DrillRequest):
    request_id: Identifier


class AnswerDrill(DrillRequest):
    event_id: Identifier
    item_id: Identifier
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]


def language_drill_router(services: Container) -> APIRouter:
    router = APIRouter()
    drills = services.language_drills

    @router.get("/api/language-drill")
    def overview(request: Request) -> dict[str, Any]:
        return drills.overview(request.state.learner_id)

    @router.post("/api/language-drill/drills", status_code=201)
    def start(body: StartDrill, request: Request) -> dict[str, Any]:
        return drills.start(request.state.learner_id, body.request_id)

    @router.get("/api/language-drill/drills/{drill_id}")
    def get(drill_id: str, request: Request) -> dict[str, Any]:
        return drills.get(request.state.learner_id, drill_id)

    @router.post("/api/language-drill/drills/{drill_id}/answers")
    def answer(drill_id: str, body: AnswerDrill, request: Request) -> dict[str, Any]:
        return drills.answer(
            request.state.learner_id,
            drill_id,
            event_id=body.event_id,
            item_id=body.item_id,
            text=body.text,
        )

    @router.post("/api/language-drill/drills/{drill_id}/finish")
    def finish(drill_id: str, request: Request) -> dict[str, Any]:
        return drills.finish(request.state.learner_id, drill_id)

    return router
