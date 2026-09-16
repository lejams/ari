"""Personal lexicon API: owned by the authenticated profile, review events idempotent."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ari.application.ports.lexicon import LexiconIngestReport
from ari.container import Container
from ari.domain.clinical import Identifier
from ari.domain.models import LexiconEntry, SrsRating, utc_now


class LexiconRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AddEntry(LexiconRequest):
    lemma: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
    translation: Annotated[str, Field(max_length=120)] = ""
    example: Annotated[str, Field(max_length=250)] = ""


class ReviewEntry(LexiconRequest):
    event_id: Identifier
    rating: Literal["again", "hard", "good", "easy"]


class ArchiveEntry(LexiconRequest):
    archived: bool


def public_entry(entry: LexiconEntry, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    return {
        "id": entry.id,
        "lemma": entry.lemma,
        "translation": entry.translation,
        "example": entry.example,
        "source": entry.source.value,
        "state": entry.state.value,
        "archived": entry.archived,
        "due": entry.srs.due_at <= now,
        "due_at": entry.srs.due_at.isoformat(),
        "interval_days": entry.srs.interval_days,
        "repetitions": entry.srs.repetitions,
        "lapses": entry.srs.lapses,
        "used_sessions": len(entry.used_session_ids),
        "first_session_id": entry.first_session_id,
        "created_at": entry.created_at.isoformat(),
    }


def public_report(report: LexiconIngestReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "added": [public_entry(e) for e in report.added],
        "promoted": [public_entry(e) for e in report.promoted],
        "wrong_language_turns": list(report.wrong_language_turns),
        "srs_version": report.srs_version,
    }


def lexicon_router(services: Container) -> APIRouter:
    router = APIRouter()
    lexicon = services.lexicon

    @router.get("/api/lexicon")
    def overview(request: Request) -> dict[str, Any]:
        result = lexicon.overview(request.state.learner_id)
        now = utc_now()
        return {
            "srs_version": result.srs_version,
            "due_count": len(result.due),
            "by_state": dict(result.by_state),
            "entries": [public_entry(e, now) for e in result.entries],
            "limitations": (
                "Un mot devient « utilisé » quand vous le prononcez spontanément dans une "
                "session ultérieure, et « maîtrisé » après deux sessions distinctes et trois "
                "révisions réussies. La reconnaissance est lexicale, pas un jugement de "
                "correction."
            ),
        }

    @router.post("/api/lexicon/entries", status_code=201)
    def add(body: AddEntry, request: Request) -> dict[str, Any]:
        return public_entry(
            lexicon.add_manual(request.state.learner_id, body.lemma, body.translation, body.example)
        )

    @router.post("/api/lexicon/entries/{entry_id}/reviews")
    def review(entry_id: str, body: ReviewEntry, request: Request) -> dict[str, Any]:
        return public_entry(
            lexicon.review(
                request.state.learner_id, entry_id, body.event_id, SrsRating(body.rating)
            )
        )

    @router.patch("/api/lexicon/entries/{entry_id}")
    def archive(entry_id: str, body: ArchiveEntry, request: Request) -> dict[str, Any]:
        return public_entry(lexicon.set_archived(request.state.learner_id, entry_id, body.archived))

    return router
