"""Progression by axis: structure, communication, language, lexicon, regularity, level.

Every number here is deterministic and traceable to sessions, reviews or attempts. LLM
prose never enters; verdicts and error categories appear only as counts. No global score.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from ari.application.services.lexicon import LexiconOverview
from ari.domain.clinical import ANAMNESIS_SECTION_IDS
from ari.domain.models import ConversationSession, Evaluation, SessionStatus
from ari.domain.placement import PlacementAttempt
from ari.domain.practice import PracticeRun

PROGRESS_AXES_VERSION = "progress-axes-v1"
HEATMAP_SESSIONS = 10
WINDOW = 5
WEEKS = 4


def _eval(session: ConversationSession) -> Evaluation:
    assert session.evaluation is not None  # _completed keeps evaluated sessions only
    return session.evaluation


def _completed(sessions: tuple[ConversationSession, ...]) -> list[ConversationSession]:
    done = [s for s in sessions if s.status is SessionStatus.COMPLETED and s.evaluation is not None]
    return sorted(done, key=lambda s: (s.created_at, s.id))


def _structure_axis(sessions: list[ConversationSession]) -> dict[str, Any]:
    recent = [s for s in sessions if _eval(s).structure][-HEATMAP_SESSIONS:]
    labels: dict[str, str] = {}
    rows = []
    totals: dict[str, list[int]] = {}
    for session in recent:
        structure = _eval(session).structure or {}
        cells: dict[str, float | None] = {}
        for section in structure["sections"]:
            labels[section["id"]] = section["label"]
            total = len(section["fact_ids"])
            covered = len(section["covered_fact_ids"])
            cells[section["id"]] = covered / total if total else None
            bucket = totals.setdefault(section["id"], [0, 0])
            bucket[0] += covered
            bucket[1] += total
        rows.append(
            {
                "session_id": session.id,
                "date": session.created_at.isoformat(),
                "mode": session.learning_mode.value if session.learning_mode else "unknown",
                "cells": cells,
                "order_respected": structure["canonical_order_respected"],
            }
        )
    columns = [{"id": sid, "label": labels[sid]} for sid in ANAMNESIS_SECTION_IDS if sid in labels]
    averages = {sid: (v[0] / v[1] if v[1] else None) for sid, v in totals.items()}
    weakest = sorted(
        (sid for sid, ratio in averages.items() if ratio is not None),
        key=lambda sid: (averages[sid], sid),
    )
    return {
        "sessions": len(recent),
        "columns": columns,
        "rows": rows,
        "averages": averages,
        "weakest": [
            {"id": sid, "label": labels[sid], "ratio": averages[sid]} for sid in weakest[:2]
        ],
        "order_respected_rate": (
            sum(1 for r in rows if r["order_respected"]) / len(rows) if rows else None
        ),
    }


def _communication_axis(sessions: list[ConversationSession]) -> dict[str, Any]:
    timeline = []
    for session in sessions[-HEATMAP_SESSIONS:]:
        for moment in _eval(session).empathy:
            if moment["verdict"] in {"acknowledged", "partial", "ignored"}:
                timeline.append(
                    {
                        "session_id": session.id,
                        "date": session.created_at.isoformat(),
                        "cue": moment["cue"],
                        "verdict": moment["verdict"],
                    }
                )
    judged = [t["verdict"] for t in timeline]
    recent, previous = judged[-WINDOW:], judged[-2 * WINDOW : -WINDOW]

    def rate(values: list[str]) -> float | None:
        return values.count("acknowledged") / len(values) if values else None

    return {
        "timeline": timeline,
        "counts": dict(Counter(judged)),
        "acknowledged_rate": rate(recent),
        "previous_rate": rate(previous),
    }


def _language_axis(sessions: list[ConversationSession]) -> dict[str, Any]:
    def categories(window: list[ConversationSession]) -> Counter[str]:
        counter: Counter[str] = Counter()
        for session in window:
            for error in _eval(session).language_errors:
                counter[error.category or "other"] += 1
        return counter

    recent, previous = sessions[-WINDOW:], sessions[-2 * WINDOW : -WINDOW]
    recent_counts, previous_counts = categories(recent), categories(previous)
    code_switches = sum(len(_eval(s).code_switches) for s in recent)
    examples: dict[str, list[dict[str, Any]]] = {}
    for session in recent:
        for error in _eval(session).language_errors:
            examples.setdefault(error.category or "other", []).append(
                {
                    "session_id": session.id,
                    "text": error.text,
                    "turns": list(error.evidence_turn_sequences),
                }
            )
    return {
        "sessions_considered": len(recent),
        "by_category": dict(recent_counts),
        "previous_by_category": dict(previous_counts),
        "recurring": [
            {"category": category, "count": count, "examples": examples.get(category, [])[-3:]}
            for category, count in recent_counts.most_common()
            if count >= 2
        ],
        "errors_per_session": (
            round(sum(recent_counts.values()) / len(recent), 2) if recent else None
        ),
        "previous_errors_per_session": (
            round(sum(previous_counts.values()) / len(previous), 2) if previous else None
        ),
        "code_switches": code_switches,
    }


def _lexicon_axis(overview: LexiconOverview, now: datetime) -> dict[str, Any]:
    month_ago = now - timedelta(days=30)
    return {
        "active": len(overview.active),
        "acquired": len(overview.acquired),
        "acquired_last_30_days": sum(1 for e in overview.acquired if e.updated_at >= month_ago),
        "added_last_30_days": sum(1 for e in overview.entries if e.created_at >= month_ago),
        "due": len(overview.due),
        "maintenance_due": len(overview.due_maintenance),
        "by_state": dict(overview.by_state),
    }


def _regularity_axis(
    sessions: list[ConversationSession], runs: tuple[PracticeRun, ...], now: datetime
) -> dict[str, Any]:
    weeks = []
    for index in range(WEEKS):
        end = now - timedelta(days=7 * index)
        start = end - timedelta(days=7)
        voice = [s for s in sessions if s.ended_at and start < s.ended_at <= end]
        practice = [
            r for r in runs if r.status == "completed" and r.ended_at and start < r.ended_at <= end
        ]
        minutes = sum(
            max(1, int((s.ended_at - s.call_started_at).total_seconds() // 60))
            for s in voice
            if s.call_started_at and s.ended_at
        )
        weeks.append(
            {
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "voice_sessions": len(voice),
                "practice_runs": len(practice),
                "voice_minutes": minutes,
            }
        )
    return {"weeks": list(reversed(weeks))}


def _level_axis(attempts: tuple[PlacementAttempt, ...]) -> dict[str, Any]:
    history = [
        {
            "attempt_id": a.id,
            "date": a.ended_at.isoformat() if a.ended_at else a.created_at.isoformat(),
            "band": a.result["band"],
            "vocab_grammar": a.result["vocab_grammar_level"],
            "listening": a.result["listening_level"],
            "speaking": a.result["speaking_level"],
            "speaking_counted": a.result["speaking_counted"],
        }
        for a in sorted(attempts, key=lambda a: a.created_at)
        if a.status == "completed" and a.result
    ]
    return {"history": history, "state": "estimated"}


def progress_axes(
    sessions: tuple[ConversationSession, ...],
    runs: tuple[PracticeRun, ...],
    overview: LexiconOverview,
    attempts: tuple[PlacementAttempt, ...],
    now: datetime,
) -> dict[str, Any]:
    completed = _completed(sessions)
    return {
        "version": PROGRESS_AXES_VERSION,
        "sessions_completed": len(completed),
        "structure": _structure_axis(completed),
        "communication": _communication_axis(completed),
        "language": _language_axis(completed),
        "lexicon": _lexicon_axis(overview, now),
        "regularity": _regularity_axis(completed, runs, now),
        "level": _level_axis(attempts),
        "limitations": (
            "Axes calculés à partir de vos sessions terminées, de votre carnet et de vos tests "
            "de niveau. Les catégories d'erreurs viennent de l'évaluation automatique et "
            "restent indicatives ; aucun score global n'est calculé."
        ),
    }
