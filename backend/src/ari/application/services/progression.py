"""Conservative comparable series, never an invented aggregate/global score."""

import json
from typing import Any

from ari.application.ports.cases import MedicalCaseCatalog
from ari.application.services.practice import content_summary
from ari.domain.errors import AriError
from ari.domain.models import ConversationSession
from ari.domain.practice import PracticeRun

# v3 adds qualitative fields (code switches, candidate kinds); the deterministic
# criteria and their scoring method are unchanged, so both versions compare.
COMPARABLE_EVALUATION_SCHEMAS = frozenset({"session-evaluation-v2", "session-evaluation-v3"})


def practice_history(runs: tuple[PracticeRun, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": run.id,
            "kind": "practice",
            "mode": run.mode,
            "status": run.status,
            "created_at": run.created_at.isoformat(),
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
            "content": content_summary(run.content),
            "feedback_state": run.feedback.state if run.feedback else "no_data",
            "answered": len(run.answers),
            "has_feedback": run.feedback is not None,
        }
        for run in runs
    ]


def practice_progression(runs: tuple[PracticeRun, ...]) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for run in sorted(runs, key=lambda r: (r.created_at, r.id)):
        if run.feedback is None:
            continue  # Also prevents Exam feedback leaking through progression before completion.
        # The content hash already covers every dependency hash, source and provenance.
        group = groups.setdefault(
            (run.content.content_hash, run.mode),
            {
                "kind": "practice",
                "content": content_summary(run.content),
                "mode": run.mode,
                "scoring_version": run.feedback.scoring_version,
                "points": [],
            },
        )
        group["points"].append(
            {
                "run_id": run.id,
                "created_at": run.created_at.isoformat(),
                "state": run.feedback.state,
                "dimensions": [d.model_dump(mode="json") for d in run.feedback.dimensions],
            }
        )
    return {
        "state": "available" if groups else "no_data",
        "groups": list(groups.values()),
        "limitations": "Séries séparées par contenu/version, rubrique, méthode, "
        "provenance et mode. "
        "Pas de moyenne globale. Comparer aussi les poids répondus/attendus. "
        "Les démos et exercices incomplets restent provisoires.",
    }


def voice_history(sessions: tuple[ConversationSession, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "kind": "voice",
            "mode": s.learning_mode.value if s.learning_mode else "unknown",
            "status": s.status.value,
            "created_at": s.created_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "content": {
                "title": f"Session vocale · {s.case_id}@{s.case_version}",
                "phase": "arzt_patient",
                "case_hash": s.case_hash,
                **dict(s.training_snapshot),
            },
            "feedback_state": "provisional" if s.evaluation else "no_data",
            "answered": len(s.turns),
            "has_feedback": s.evaluation is not None,
        }
        for s in sessions
    ]


def voice_progression(
    sessions: tuple[ConversationSession, ...],
    cases: MedicalCaseCatalog,
) -> dict[str, Any]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    excluded = []
    for session in sorted(sessions, key=lambda s: (s.created_at, s.id)):
        evaluation = session.evaluation
        if evaluation is None:
            continue
        # The historical fact-count score remains readable in its original session,
        # but is not silently promoted to comparable measured progression.
        if (
            evaluation.schema_version not in COMPARABLE_EVALUATION_SCHEMAS
            or not session.training_snapshot
        ):
            excluded.append(
                {"run_id": session.id, "reason": "Évaluation historique non comparable"}
            )
            continue
        try:
            case = cases.get(
                session.case_id,
                session.case_version,
                scenario_id=session.training_snapshot.get("scenario_id"),
                scenario_version=session.training_snapshot.get("scenario_version"),
            )
        except AriError:
            excluded.append({"run_id": session.id, "reason": "Contenu épinglé indisponible"})
            continue
        if case.content_hash != session.case_hash or dict(case.training_snapshot) != dict(
            session.training_snapshot
        ):
            excluded.append({"run_id": session.id, "reason": "Intégrité de version non vérifiable"})
            continue
        rubric = {d.id: d for d in case.rubric}
        if evaluation.rubric_version != case.rubric_version:
            excluded.append({"run_id": session.id, "reason": "Version de rubrique incohérente"})
            continue
        methods = {c.get("scoring_version") for c in evaluation.criteria}
        if methods != {"assessment-weighted-v1"} or {
            c.get("criterion_id") for c in evaluation.criteria
        } != set(rubric):
            excluded.append({"run_id": session.id, "reason": "Méthode de mesure incompatible"})
            continue
        # Source/model/prompt and audio stack context also separate these series.
        provenance = sorted(
            {
                (e.provider, e.model, e.prompt_version, e.prompt_hash)
                for e in session.executions
                if e.operation == "session_evaluation"
            },
            key=str,
        )
        key = (
            session.case_hash,
            json.dumps(dict(session.training_snapshot), sort_keys=True),
            str(session.learning_mode),
            evaluation.prompt_version,
            evaluation.rubric_version,
            json.dumps(provenance, sort_keys=True),
            json.dumps(dict(session.voice_stack_config), sort_keys=True),
        )
        state = "provisional"  # Automated voice evidence, never a human clinical approval.
        group = groups.setdefault(
            key,
            {
                "kind": "voice",
                "mode": session.learning_mode or "unknown",
                "content": {
                    "title": case.title,
                    "phase": "arzt_patient",
                    **dict(session.training_snapshot),
                },
                "scoring_version": "assessment-weighted-v1",
                "points": [],
            },
        )
        group["points"].append(
            {
                "run_id": session.id,
                "created_at": session.created_at.isoformat(),
                "state": state,
                "dimensions": [
                    {
                        "id": c["criterion_id"],
                        "label": rubric[c["criterion_id"]].label,
                        "score": c["score"] if session.turns else None,
                        "max_score": rubric[c["criterion_id"]].max_score,
                        "state": state if session.turns else "no_data",
                        "feedback": c["feedback"],
                        "evidence_turn_sequences": c["evidence_turn_sequences"],
                    }
                    for c in evaluation.criteria
                ],
            }
        )
    return {"groups": list(groups.values()), "excluded": excluded}
