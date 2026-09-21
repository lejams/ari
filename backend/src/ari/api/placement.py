"""Placement test API: owned attempts, idempotent answers, listening audio as WAV."""

import io
import wave
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ari.container import Container
from ari.domain.clinical import Identifier
from ari.domain.errors import InvalidStateError

MAX_SPEAKING_BYTES = 24_000 * 2 * 180  # three minutes of PCM16 mono at 24 kHz


class PlacementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StartPlacement(PlacementRequest):
    request_id: Identifier


class AnswerPlacement(PlacementRequest):
    event_id: Identifier
    item_id: Identifier
    option_index: Annotated[int, Field(ge=0, le=3)]


def wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as container:
        container.setnchannels(1)
        container.setsampwidth(2)
        container.setframerate(sample_rate)
        container.writeframes(pcm16)
    return buffer.getvalue()


def placement_router(services: Container) -> APIRouter:
    router = APIRouter()
    placement = services.placement

    @router.get("/api/placement")
    def overview(request: Request) -> dict[str, Any]:
        return placement.overview(request.state.learner_id)

    @router.post("/api/placement/attempts", status_code=201)
    def start(body: StartPlacement, request: Request) -> dict[str, Any]:
        return placement.start(request.state.learner_id, body.request_id)

    @router.get("/api/placement/attempts/{attempt_id}")
    def get(attempt_id: str, request: Request) -> dict[str, Any]:
        return placement.get(request.state.learner_id, attempt_id)

    @router.post("/api/placement/attempts/{attempt_id}/answers")
    def answer(attempt_id: str, body: AnswerPlacement, request: Request) -> dict[str, Any]:
        return placement.answer(
            request.state.learner_id,
            attempt_id,
            event_id=body.event_id,
            item_id=body.item_id,
            option_index=body.option_index,
        )

    @router.get("/api/placement/attempts/{attempt_id}/items/{item_id}/audio")
    async def audio(attempt_id: str, item_id: str, request: Request) -> Response:
        pcm16 = await placement.listening_audio(request.state.learner_id, attempt_id, item_id)
        return Response(
            content=wav_bytes(pcm16, placement.sample_rate),
            media_type="audio/wav",
            headers={"Cache-Control": "private, max-age=600"},
        )

    @router.post("/api/placement/attempts/{attempt_id}/speaking/{item_id}")
    async def speaking(attempt_id: str, item_id: str, request: Request) -> dict[str, Any]:
        """Body: PCM16 mono 24 kHz (application/octet-stream), or text/plain in fake mode."""
        event_id = request.headers.get("x-event-id", "")
        if not event_id or len(event_id) > 120:
            raise InvalidStateError("En-tête X-Event-Id requis")
        body = await request.body()
        if len(body) > MAX_SPEAKING_BYTES:
            raise InvalidStateError("Enregistrement trop long")
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type == "text/plain":
            if services.settings.provider_mode != "fake":
                raise InvalidStateError("La réponse écrite n'est acceptée qu'en mode fake")
            return await placement.speak(
                request.state.learner_id,
                attempt_id,
                event_id=event_id,
                item_id=item_id,
                text=body.decode("utf-8", errors="replace"),
            )
        return await placement.speak(
            request.state.learner_id, attempt_id, event_id=event_id, item_id=item_id, pcm16=body
        )

    @router.post("/api/placement/attempts/{attempt_id}/finish")
    def finish(attempt_id: str, request: Request) -> dict[str, Any]:
        return placement.finish(request.state.learner_id, attempt_id)

    return router
