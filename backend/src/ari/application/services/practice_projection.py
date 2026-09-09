"""Conversion of legacy domain objects to the common practice projection."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ari.domain.models import ConversationSession, ConversationTurn
from ari.domain.practice import PracticeRun
from ari.domain.practice_lifecycle import (
    CanonicalResponse,
    NormalizedText,
    PracticeFeedback,
    PracticeHandle,
    PracticeHandleKind,
    PracticeModality,
    PracticeStateProjection,
    PracticeTransport,
    PracticeTurn,
)


def normalize_user_text(text: str) -> NormalizedText:
    # Preserve medical content: only Unicode normalization and whitespace are
    # changed.  In particular, negation, digits and punctuation are retained.
    import re
    import unicodedata

    return NormalizedText(
        re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).strip()),
    )


def _execution_cost(executions: tuple[Any, ...]) -> float | None:
    values = [e.cost_amount_usd for e in executions if e.cost_amount_usd is not None]
    return sum(values) if values else None


def project_voice(session: ConversationSession) -> PracticeStateProjection:
    transport = PracticeTransport.REALTIME if str(
        session.voice_stack_config.get("transport", "pipeline")
    ) == "realtime" else PracticeTransport.PIPELINE
    snapshot = session.training_snapshot or {}
    handle = PracticeHandle(
        id=session.id,
        learner_id=session.learner_id,
        kind=PracticeHandleKind.PATIENT_VOICE,
        modality=PracticeModality.VOICE,
        transport=transport,
        status=session.status.value,
        case_id=session.case_id,
        case_version=session.case_version,
        scenario_id=(str(snapshot["scenario_id"]) if snapshot.get("scenario_id") else None),
        scenario_version=(
            str(snapshot["scenario_version"]) if snapshot.get("scenario_version") else None
        ),
        created_at=session.created_at,
    )
    turns = tuple(project_voice_turn(session, turn) for turn in session.turns)
    feedback = None
    if session.evaluation is not None:
        feedback = PracticeFeedback(
            state="evaluated",
            payload=asdict(session.evaluation),
            limitations=("Evaluation remains grounded in the historical voice rubric.",),
        )
    return PracticeStateProjection(handle, turns, feedback, session.status.value)


def project_voice_turn(session: ConversationSession, turn: ConversationTurn) -> PracticeTurn:
    canonical = turn.canonical_response
    response = (
        CanonicalResponse(
            text=str(canonical.get("text", turn.patient_text)),
            version=str(canonical.get("version", "canonical-response-v1")),
            selected_fact_ids=tuple(canonical.get("selected_fact_ids", turn.selected_fact_ids)),
            source_refs=tuple(canonical.get("source_refs", ())),
            response_kind=str(canonical.get("response_kind", "text")),
        )
        if canonical is not None
        else None
    )
    executions = tuple(e for e in session.executions if e.turn_id == turn.id)
    snapshot = session.training_snapshot or {}
    simulation_id = str(snapshot.get("scenario_id") or session.case_id)
    return PracticeTurn(
        id=turn.id,
        practice_id=session.id,
        sequence=turn.sequence,
        simulation_id=simulation_id,
        modality=PracticeModality.VOICE,
        raw_user_text=turn.user_text,
        normalized_user_text=(
            NormalizedText(turn.normalized_user_text)
            if turn.normalized_user_text
            else None
        ),
        canonical_response=response,
        context_fact_ids=tuple(turn.revealed_fact_ids),
        selected_fact_ids=tuple(turn.selected_fact_ids),
        revealed_fact_ids=tuple(turn.revealed_fact_ids),
        executions=executions,
        cost_usd=_execution_cost(executions),
        latency_ms=sum(e.latency_ms for e in executions) if executions else None,
        observed_response_text=turn.observed_response_text,
        decisions=(
            ({"provenance": "legacy"},)
            if turn.canonical_response is None
            else ({"provenance": "patient_simulation"},)
        ),
        created_at=turn.created_at,
    )


def project_structured(run: PracticeRun) -> PracticeStateProjection:
    handle = PracticeHandle(
        id=run.id,
        learner_id=run.learner_id,
        kind=PracticeHandleKind.STRUCTURED_TEXT,
        modality=PracticeModality.TEXT,
        transport=PracticeTransport.NONE,
        status=run.status,
        request_id=run.request_id,
        scenario_id=run.content.scenario_id,
        scenario_version=run.content.scenario_version,
        created_at=run.created_at,
    )
    turns = tuple(
        PracticeTurn(
            id=a.event_id,
            practice_id=run.id,
            sequence=index,
            simulation_id=run.content.scenario_id,
            modality=PracticeModality.TEXT,
            raw_user_text=a.text,
            normalized_user_text=normalize_user_text(a.text),
            decisions=({"provenance": "structured_text", "question_id": a.question_id},),
            created_at=a.submitted_at,
        )
        for index, a in enumerate(run.answers, 1)
    )
    feedback = (
        PracticeFeedback(
            state=run.feedback.state,
            payload=run.feedback.model_dump(mode="json"),
        )
        if run.feedback else None
    )
    return PracticeStateProjection(handle, turns, feedback, run.status)
