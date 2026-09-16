"""Learner lexicon persistence: mutable entries, append-only reviews, one report per session."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.application.ports.lexicon import LexiconIngestReport
from ari.domain.errors import NotFoundError
from ari.domain.models import (
    LexiconEntry,
    LexiconReview,
    LexiconSource,
    SrsRating,
    SrsState,
    VocabularyState,
)
from ari.infrastructure.persistence.lexicon_rows import (
    LexiconEntryRow,
    LexiconReviewRow,
    SessionLexiconReportRow,
)


def _dt(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _parse(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    return _dt(datetime.fromisoformat(value) if isinstance(value, str) else value)


def _srs_payload(srs: SrsState) -> dict[str, Any]:
    return {
        "due_at": srs.due_at.isoformat(),
        "interval_days": srs.interval_days,
        "ease": srs.ease,
        "repetitions": srs.repetitions,
        "lapses": srs.lapses,
        "last_reviewed_at": srs.last_reviewed_at.isoformat() if srs.last_reviewed_at else None,
    }


def _srs(payload: dict[str, Any]) -> SrsState:
    due_at = _parse(payload["due_at"])
    assert due_at is not None
    return SrsState(
        due_at=due_at,
        interval_days=int(payload.get("interval_days", 0)),
        ease=float(payload.get("ease", 2.5)),
        repetitions=int(payload.get("repetitions", 0)),
        lapses=int(payload.get("lapses", 0)),
        last_reviewed_at=_parse(payload.get("last_reviewed_at")),
    )


def _entry_payload(entry: LexiconEntry) -> dict[str, Any]:
    """Snapshot stored in session reports, so feedback never depends on later edits."""
    return {
        "id": entry.id,
        "learner_id": entry.learner_id,
        "lemma_key": entry.lemma_key,
        "lemma": entry.lemma,
        "translation": entry.translation,
        "example": entry.example,
        "source": entry.source.value,
        "state": entry.state.value,
        "srs": _srs_payload(entry.srs),
        "first_session_id": entry.first_session_id,
        "last_session_id": entry.last_session_id,
        "used_session_ids": list(entry.used_session_ids),
        "archived": entry.archived,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _entry_from_payload(value: dict[str, Any]) -> LexiconEntry:
    created_at = _parse(value["created_at"])
    updated_at = _parse(value["updated_at"])
    assert created_at is not None and updated_at is not None
    return LexiconEntry(
        id=str(value["id"]),
        learner_id=str(value["learner_id"]),
        lemma_key=str(value["lemma_key"]),
        lemma=str(value["lemma"]),
        translation=str(value["translation"]),
        example=str(value["example"]),
        source=LexiconSource(str(value["source"])),
        state=VocabularyState(str(value["state"])),
        srs=_srs(dict(value["srs"])),
        first_session_id=value.get("first_session_id"),
        last_session_id=value.get("last_session_id"),
        used_session_ids=tuple(value.get("used_session_ids", ())),
        archived=bool(value.get("archived", False)),
        created_at=created_at,
        updated_at=updated_at,
    )


class SqlLexiconRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _entry(row: LexiconEntryRow) -> LexiconEntry:
        return LexiconEntry(
            id=row.id,
            learner_id=row.learner_id,
            lemma_key=row.lemma_key,
            lemma=row.lemma,
            translation=row.translation,
            example=row.example,
            source=LexiconSource(row.source),
            state=VocabularyState(row.state),
            srs=_srs(row.srs),
            first_session_id=row.first_session_id,
            last_session_id=row.last_session_id,
            used_session_ids=tuple(row.used_session_ids),
            archived=row.archived,
            created_at=_dt(row.created_at),
            updated_at=_dt(row.updated_at),
        )

    @staticmethod
    def _apply(row: LexiconEntryRow, entry: LexiconEntry) -> None:
        row.learner_id = entry.learner_id
        row.lemma_key = entry.lemma_key
        row.lemma = entry.lemma
        row.translation = entry.translation
        row.example = entry.example
        row.source = entry.source.value
        row.state = entry.state.value
        row.srs = _srs_payload(entry.srs)
        row.first_session_id = entry.first_session_id
        row.last_session_id = entry.last_session_id
        row.used_session_ids = list(entry.used_session_ids)
        row.archived = entry.archived
        row.created_at = entry.created_at
        row.updated_at = entry.updated_at

    def get(self, learner_id: str, entry_id: str) -> LexiconEntry:
        with Session(self.engine) as db:
            row = db.get(LexiconEntryRow, entry_id)
            if row is None or row.learner_id != learner_id:
                raise NotFoundError("Entrée de vocabulaire introuvable")
            return self._entry(row)

    def list(self, learner_id: str, *, include_archived: bool = False) -> tuple[LexiconEntry, ...]:
        with Session(self.engine) as db:
            statement = select(LexiconEntryRow).where(LexiconEntryRow.learner_id == learner_id)
            if not include_archived:
                statement = statement.where(LexiconEntryRow.archived.is_(False))
            rows = db.scalars(statement.order_by(LexiconEntryRow.created_at, LexiconEntryRow.id))
            return tuple(self._entry(row) for row in rows)

    def upsert(self, entries: Iterable[LexiconEntry]) -> None:
        with Session(self.engine) as db, db.begin():
            for entry in entries:
                row = db.get(LexiconEntryRow, entry.id)
                if row is None:
                    row = db.scalar(
                        select(LexiconEntryRow).where(
                            LexiconEntryRow.learner_id == entry.learner_id,
                            LexiconEntryRow.lemma_key == entry.lemma_key,
                        )
                    )
                if row is None:
                    row = LexiconEntryRow(id=entry.id)
                    db.add(row)
                self._apply(row, entry)

    def find_review(self, entry_id: str, event_id: str) -> LexiconReview | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(LexiconReviewRow).where(
                    LexiconReviewRow.entry_id == entry_id, LexiconReviewRow.event_id == event_id
                )
            )
            if row is None:
                return None
            return LexiconReview(
                id=row.id,
                entry_id=row.entry_id,
                event_id=row.event_id,
                rating=SrsRating(row.rating),
                reviewed_at=_dt(row.reviewed_at),
            )

    def record_review(self, review: LexiconReview, entry: LexiconEntry) -> LexiconEntry:
        """One review event updates the entry exactly once; a replay returns the stored entry."""
        try:
            with Session(self.engine) as db, db.begin():
                row = db.get(LexiconEntryRow, entry.id)
                if row is None or row.learner_id != entry.learner_id:
                    raise NotFoundError("Entrée de vocabulaire introuvable")
                db.add(
                    LexiconReviewRow(
                        id=review.id,
                        entry_id=review.entry_id,
                        event_id=review.event_id,
                        rating=review.rating.value,
                        reviewed_at=review.reviewed_at,
                    )
                )
                db.flush()
                self._apply(row, entry)
        except IntegrityError:
            pass  # The same event was recorded concurrently; the stored entry wins.
        return self.get(entry.learner_id, entry.id)

    def save_report(self, report: LexiconIngestReport) -> None:
        with Session(self.engine) as db, db.begin():
            db.merge(
                SessionLexiconReportRow(
                    session_id=report.session_id,
                    payload={
                        "session_id": report.session_id,
                        "added": [_entry_payload(e) for e in report.added],
                        "promoted": [_entry_payload(e) for e in report.promoted],
                        "wrong_language_turns": list(report.wrong_language_turns),
                        "srs_version": report.srs_version,
                    },
                )
            )

    def get_report(self, session_id: str) -> LexiconIngestReport | None:
        with Session(self.engine) as db:
            row = db.get(SessionLexiconReportRow, session_id)
            if row is None:
                return None
            payload = row.payload
            return LexiconIngestReport(
                session_id=str(payload["session_id"]),
                added=tuple(_entry_from_payload(e) for e in payload.get("added", [])),
                promoted=tuple(_entry_from_payload(e) for e in payload.get("promoted", [])),
                wrong_language_turns=tuple(int(t) for t in payload.get("wrong_language_turns", [])),
                srs_version=str(payload.get("srs_version", "")),
            )
