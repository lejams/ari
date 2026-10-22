"""A recorded transcript replayed as a completed session, for the session evaluator suites."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ari.domain.models import (
    AudioDeliveryStatus,
    CEFRLevel,
    ConversationSession,
    ConversationTurn,
    LearningGoal,
    MedicalCase,
    PatientResponseKind,
    SessionStatus,
)


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: Annotated[int, Field(ge=1)]
    doctor: Annotated[str, Field(min_length=1)]
    patient: str
    revealed_fact_ids: list[str] = Field(default_factory=list)
    patient_response_kind: PatientResponseKind = PatientResponseKind.SOURCES


def check_transcript(item_id: str, turns: list[TranscriptTurn], case: MedicalCase) -> None:
    sequences = [turn.sequence for turn in turns]
    if sequences != list(range(1, len(turns) + 1)):
        raise ValueError(f"{item_id}: turns are numbered 1..n in order")
    unknown = {f for turn in turns for f in turn.revealed_fact_ids} - case.fact_ids
    if unknown:
        raise ValueError(f"{item_id}: unknown fact ids {sorted(unknown)}")


def session_from_transcript(
    run_key: str, case: MedicalCase, turns: list[TranscriptTurn], target_cefr: CEFRLevel
) -> ConversationSession:
    """Every patient answer counts as heard, like a session whose audio was all delivered."""
    return ConversationSession(
        id=run_key,
        learner_id="evals",
        case_id=case.id,
        case_version=case.version,
        case_hash=case.content_hash,
        goal=LearningGoal(target_cefr=target_cefr),
        status=SessionStatus.ANALYSIS_PENDING,
        turns=tuple(
            ConversationTurn(
                id=f"{run_key}:t{turn.sequence}",
                session_id=run_key,
                sequence=turn.sequence,
                user_text=turn.doctor,
                patient_text=turn.patient,
                revealed_fact_ids=tuple(turn.revealed_fact_ids),
                delivery_status=AudioDeliveryStatus.DELIVERED,
                patient_response_kind=turn.patient_response_kind.value,
            )
            for turn in turns
        ),
    )
