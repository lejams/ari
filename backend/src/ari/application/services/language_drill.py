"""The remediation drill service: start from the learner's recent errors, answer, finish."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ari.application.ports.language_drill import LanguageDrillRepository
from ari.application.ports.repository import SessionRepository
from ari.domain.errors import InvalidStateError
from ari.domain.language_drill import (
    CATEGORY_LABELS,
    DrillAnswer,
    DrillItem,
    LanguageDrill,
    check_answer,
    language_profile,
    select_items,
)
from ari.domain.models import new_id, utc_now


def matched_item_ids(drills: tuple[LanguageDrill, ...]) -> frozenset[str]:
    """The mistakes a learner already said right in a drill: they are not asked again."""
    return frozenset(i for drill in drills for i in drill.matched_item_ids)


def _item_public(item: DrillItem, answer: DrillAnswer | None) -> dict[str, Any]:
    public: dict[str, Any] = {
        "id": item.id,
        "category": item.category,
        "category_label": CATEGORY_LABELS.get(item.category, item.category),
        "quote": item.quote,
        "answered": answer is not None,
    }
    if answer is not None:  # The correction is never shown before the learner has tried.
        public.update(
            correction=item.correction,
            note=item.note,
            your_answer=answer.text,
            outcome=answer.outcome,
        )
    return public


def public_drill(drill: LanguageDrill) -> dict[str, Any]:
    by_item = {a.item_id: a for a in drill.answers}
    answered = len(drill.answers)
    current = (
        drill.items[answered] if drill.status == "active" and answered < len(drill.items) else None
    )
    matched = sum(1 for a in drill.answers if a.outcome == "matched")
    return {
        "id": drill.id,
        "version": drill.version,
        "status": drill.status,
        "focus_category": drill.focus_category,
        "focus_label": CATEGORY_LABELS.get(drill.focus_category, drill.focus_category),
        "items": [_item_public(item, by_item.get(item.id)) for item in drill.items],
        "progress": {"answered": answered, "total": len(drill.items)},
        "current_item_id": current.id if current else None,
        "result": (
            {"matched": matched, "answered": answered, "total": len(drill.items)}
            if drill.status == "completed"
            else None
        ),
        "created_at": drill.created_at.isoformat(),
        "ended_at": drill.ended_at.isoformat() if drill.ended_at else None,
        "limitations": (
            "Vos propres erreurs, rejouées. La correction est celle proposée lors de l'analyse de "
            "votre séance : une autre formulation correcte peut exister, ARI ne la compte alors "
            "ni comme juste ni comme fausse."
        ),
    }


class LanguageDrillService:
    def __init__(
        self,
        repository: LanguageDrillRepository,
        sessions: SessionRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self._sessions = sessions
        self._clock = clock

    def overview(self, learner_id: str) -> dict[str, Any]:
        drills = self.repository.list(learner_id)
        profile = language_profile(
            self._sessions.list_sessions(learner_id), matched_item_ids(drills)
        )
        active = next((d for d in drills if d.status == "active"), None)
        return {
            "available": profile.focus is not None,
            "focus_category": profile.focus,
            "focus_label": CATEGORY_LABELS.get(profile.focus) if profile.focus else None,
            "focus_errors": profile.focus_errors,
            "items": profile.drill_items,
            "sessions_considered": profile.sessions_considered,
            "active_drill_id": active.id if active else None,
            "limitations": (
                "Les exercices reprennent vos erreurs des dernières séances. Sans erreur "
                "récurrente analysée, ARI n'en propose pas."
            ),
        }

    def start(self, learner_id: str, request_id: str) -> dict[str, Any]:
        existing = self.repository.find_request(learner_id, request_id)
        if existing is not None:
            return public_drill(existing)
        drills = self.repository.list(learner_id)
        active = next((d for d in drills if d.status == "active"), None)
        if active is not None:  # Resume rather than pile up unfinished drills.
            return public_drill(active)
        sessions = self._sessions.list_sessions(learner_id)
        matched = matched_item_ids(drills)
        focus = language_profile(sessions, matched).focus
        items = select_items(sessions, focus, matched) if focus else ()
        if focus is None or not items:
            raise InvalidStateError(
                "Aucune erreur récurrente à retravailler pour le moment : faites une séance "
                "d'entraînement, ARI analysera ce que vous avez dit."
            )
        drill = self.repository.create(
            LanguageDrill(
                id=new_id(),
                learner_id=learner_id,
                request_id=request_id,
                focus_category=focus,
                items=items,
                created_at=self._clock(),
            )
        )
        return public_drill(drill)

    def get(self, learner_id: str, drill_id: str) -> dict[str, Any]:
        return public_drill(self.repository.get(drill_id, learner_id))

    def answer(
        self, learner_id: str, drill_id: str, *, event_id: str, item_id: str, text: str
    ) -> dict[str, Any]:
        drill = self.repository.get(drill_id, learner_id)
        item = next((i for i in drill.items if i.id == item_id), None)
        if item is None:
            raise InvalidStateError("Phrase inconnue")
        answer = DrillAnswer(
            event_id=event_id,
            item_id=item_id,
            text=text.strip(),
            outcome=check_answer(item, text),
            submitted_at=self._clock(),
        )
        return public_drill(self.repository.append_answer(drill_id, learner_id, answer))

    def finish(self, learner_id: str, drill_id: str) -> dict[str, Any]:
        return public_drill(self.repository.finish(drill_id, learner_id))
