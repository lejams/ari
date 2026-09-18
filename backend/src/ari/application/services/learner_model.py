"""The learner model: computed on demand from deterministic signals, never stored.

Level (declared, estimated), lexicon state, anamnesis coverage per section over the last
comparable sessions, required items missed, empathy verdict counts, recent activity and the
exam horizon. LLM prose never enters this model; verdicts appear only as counts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ari.application.ports.repository import SessionRepository
from ari.application.services.lexicon import LexiconService
from ari.domain.models import ConversationSession, LearningMode, SessionStatus
from ari.domain.placement import LEVELS
from ari.domain.practice import PracticeRun

RECENT_SESSIONS = 5
LEARNER_MODEL_VERSION = "learner-model-v1"


@dataclass(frozen=True, slots=True)
class SectionCoverage:
    id: str
    label: str
    covered: int
    total: int

    @property
    def ratio(self) -> float:
        return self.covered / self.total if self.total else 1.0


@dataclass(frozen=True, slots=True)
class LearnerModel:
    learner_id: str
    declared_level: str | None
    estimated_level: str | None
    estimated_at: datetime | None
    exam_date: date | None
    weeks_left: int | None
    minutes_per_day: int
    lexicon_total: int
    lexicon_due: int
    lexicon_by_state: dict[str, int]
    due_lemma_keys: frozenset[str]
    sections: tuple[SectionCoverage, ...]
    sessions_considered: int
    required_missed_sessions: int
    empathy_counts: dict[str, int]
    voice_training_7d: int
    voice_exam_7d: int
    practice_7d: int
    voice_minutes_7d: int
    last_completed_by_scenario: dict[str, datetime] = field(default_factory=dict)
    last_completed_by_practice: dict[str, datetime] = field(default_factory=dict)
    lexicon_acquired: int = 0
    lexicon_maintenance_due: int = 0
    land: str | None = None  # The Land the learner prepares for; recommendations favour it.

    @property
    def level_reference(self) -> str | None:
        """Estimated when measured, else declared. None means: take the placement test."""
        return self.estimated_level or self.declared_level

    @property
    def weakest_sections(self) -> tuple[SectionCoverage, ...]:
        return tuple(sorted((s for s in self.sections if s.total), key=lambda s: (s.ratio, s.id)))

    def as_public(self) -> dict[str, Any]:
        return {
            "version": LEARNER_MODEL_VERSION,
            "level": {
                "declared": self.declared_level,
                "estimated": self.estimated_level,
                "estimated_at": self.estimated_at.isoformat() if self.estimated_at else None,
                "reference": self.level_reference,
            },
            "exam": {
                "date": self.exam_date.isoformat() if self.exam_date else None,
                "weeks_left": self.weeks_left,
            },
            "minutes_per_day": self.minutes_per_day,
            "land": self.land,
            "lexicon": {
                "total": self.lexicon_total,
                "due": self.lexicon_due,
                "acquired": self.lexicon_acquired,
                "maintenance_due": self.lexicon_maintenance_due,
                "by_state": dict(self.lexicon_by_state),
            },
            "structure": {
                "sessions_considered": self.sessions_considered,
                "sections": [
                    {"id": s.id, "label": s.label, "covered": s.covered, "total": s.total}
                    for s in self.sections
                ],
            },
            "required_missed_sessions": self.required_missed_sessions,
            "empathy": dict(self.empathy_counts),
            "last_7_days": {
                "voice_training": self.voice_training_7d,
                "voice_exam": self.voice_exam_7d,
                "practice": self.practice_7d,
                "voice_minutes": self.voice_minutes_7d,
            },
        }


def level_or_none(level: str | None) -> str | None:
    return level if level in LEVELS else None


class LearnerModelService:
    def __init__(self, repository: SessionRepository, lexicon: LexiconService) -> None:
        self._repository = repository
        self._lexicon = lexicon

    def compute(
        self, learner_id: str, runs: tuple[PracticeRun, ...], now: datetime
    ) -> LearnerModel:
        learner = self._repository.get_learner(learner_id)
        details = learner.details
        sessions = tuple(
            s
            for s in self._repository.list_sessions(learner_id)
            if s.status is SessionStatus.COMPLETED
        )
        overview = self._lexicon.overview(learner_id)
        week_ago = now - timedelta(days=7)

        recent = [s for s in sorted(sessions, key=lambda s: s.created_at, reverse=True)][
            :RECENT_SESSIONS
        ]
        sections: dict[str, list[int]] = {}
        labels: dict[str, str] = {}
        required_missed = 0
        empathy: Counter[str] = Counter()
        for session in recent:
            evaluation = session.evaluation
            if evaluation is None:
                continue
            for section in (evaluation.structure or {}).get("sections", []):
                labels[section["id"]] = section["label"]
                bucket = sections.setdefault(section["id"], [0, 0])
                bucket[0] += len(section["covered_fact_ids"])
                bucket[1] += len(section["fact_ids"])
            if any(c.get("missing_required_item_ids") for c in evaluation.criteria):
                required_missed += 1
            for moment in evaluation.empathy:
                empathy[str(moment["verdict"])] += 1

        by_scenario: dict[str, datetime] = {}
        for session in sessions:
            scenario_id = session.training_snapshot.get("scenario_id")
            if scenario_id and session.ended_at:
                by_scenario[scenario_id] = max(
                    by_scenario.get(scenario_id, session.ended_at), session.ended_at
                )
        by_practice: dict[str, datetime] = {}
        for run in runs:
            if run.status == "completed" and run.ended_at:
                key = run.content.scenario_id
                by_practice[key] = max(by_practice.get(key, run.ended_at), run.ended_at)

        weeks_left = None
        if details.exam_date:
            weeks_left = max(0, (details.exam_date - now.date()).days // 7)
        return LearnerModel(
            learner_id=learner_id,
            declared_level=level_or_none(
                details.declared_level.value if details.declared_level else None
            ),
            estimated_level=level_or_none(
                details.estimated_level.value if details.estimated_level else None
            ),
            estimated_at=details.estimated_at,
            exam_date=details.exam_date,
            weeks_left=weeks_left,
            minutes_per_day=details.minutes_per_day,
            lexicon_total=len(overview.entries),
            lexicon_due=len(overview.due),
            lexicon_by_state=dict(overview.by_state),
            due_lemma_keys=frozenset(e.lemma_key for e in overview.due),
            sections=tuple(
                SectionCoverage(id=sid, label=labels[sid], covered=v[0], total=v[1])
                for sid, v in sections.items()
            ),
            sessions_considered=len(recent),
            required_missed_sessions=required_missed,
            empathy_counts=dict(empathy),
            voice_training_7d=sum(
                1
                for s in sessions
                if s.learning_mode is LearningMode.TRAINING and _within(s, week_ago)
            ),
            voice_exam_7d=sum(
                1 for s in sessions if s.learning_mode is LearningMode.EXAM and _within(s, week_ago)
            ),
            practice_7d=sum(
                1 for r in runs if r.status == "completed" and r.ended_at and r.ended_at >= week_ago
            ),
            voice_minutes_7d=sum(_minutes(s) for s in sessions if _within(s, week_ago)),
            last_completed_by_scenario=by_scenario,
            last_completed_by_practice=by_practice,
            lexicon_acquired=len(overview.acquired),
            lexicon_maintenance_due=len(overview.due_maintenance),
            land=details.land.value if details.land else None,
        )


def _within(session: ConversationSession, since: datetime) -> bool:
    return session.ended_at is not None and session.ended_at >= since


def _minutes(session: ConversationSession) -> int:
    if session.call_started_at and session.ended_at:
        return max(1, int((session.ended_at - session.call_started_at).total_seconds() // 60))
    return 0
