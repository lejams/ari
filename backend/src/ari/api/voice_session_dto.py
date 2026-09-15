"""Browser projections; internal clinical state remains in the repository.

Explicit allowlists prevent new domain fields from silently becoming public API.
"""

from typing import Any

from fastapi.encoders import jsonable_encoder

from ari.domain.models import ConversationSession, ConversationTurn, SessionStatus

TURN_FIELDS = (
    "id",
    "session_id",
    "sequence",
    "user_text",
    "patient_text",
    "provider_input_item_id",
    "provider_response_id",
    "provider_response_status",
    "response_state",
    "delivery_status",
    "audio_stream_id",
    "audio_attempt",
    "expected_audio_chunks",
    "audio_sent_at",
    "audio_started_at",
    "audio_delivered_at",
    "created_at",
)
SESSION_FIELDS = (
    "id",
    "learner_id",
    "case_id",
    "case_version",
    "case_hash",
    "voice_stack_id",
    "voice_stack_version",
    "status",
    "created_at",
    "call_started_at",
    "ended_at",
    "learning_mode",
)
SNAPSHOT_FIELDS = (
    "scenario_id",
    "scenario_version",
    "rubric_id",
    "rubric_version",
    "scoring_version",
    "provenance",
    "phase",
    "case_id",
    "case_version",
    "case_hash",
)


def _fields(value: object, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: jsonable_encoder(getattr(value, name)) for name in names}


def public_turn(turn: ConversationTurn) -> dict[str, Any]:
    return _fields(turn, TURN_FIELDS)


def public_session(session: ConversationSession) -> dict[str, Any]:
    result = _fields(session, SESSION_FIELDS)
    result["goal"] = _fields(session.goal, ("target_exam", "target_cefr", "rubric_version"))
    result["turns"] = [public_turn(turn) for turn in session.turns]
    result["training_snapshot"] = {
        name: session.training_snapshot[name]
        for name in SNAPSHOT_FIELDS
        if name in session.training_snapshot
    }
    # Keep only public model metadata; never serialize provider configuration.
    models = session.voice_stack_config.get("models", {})
    result["voice_stack_config"] = {
        "models": {
            name: models[name]
            for name in ("stt", "llm", "tts", "sts")
            if isinstance(models, dict) and isinstance(models.get(name), str)
        }
    }
    finished = session.status is SessionStatus.COMPLETED
    evaluation = session.evaluation
    result["evaluation"] = None
    if finished and evaluation:
        payload = _fields(
            evaluation,
            (
                "schema_version",
                "prompt_version",
                "rubric_version",
                "overall_score",
                "max_score",
                "summary",
                "created_at",
            ),
        )
        for name in ("strengths", "priorities", "language_errors"):
            payload[name] = [
                _fields(item, ("text", "evidence_turn_sequences"))
                for item in getattr(evaluation, name)
            ]
        payload["criteria"] = [
            {
                name: jsonable_encoder(item[name])
                for name in (
                    "criterion_id",
                    "score",
                    "evidence_turn_sequences",
                    "feedback",
                    "scoring_version",
                )
                if name in item
            }
            for item in evaluation.criteria
        ]
        result["evaluation"] = payload
    result["metrics"] = (
        _fields(
            session.metrics,
            (
                "schema_version",
                "clinical_coverage",
                "communication",
                "structure",
                "language",
                "vocabulary",
                "pronunciation_status",
            ),
        )
        if finished and session.metrics
        else None
    )
    result["vocabulary"] = (
        [
            _fields(
                item,
                (
                    "id",
                    "session_id",
                    "lemma",
                    "translation",
                    "example",
                    "evidence_turn_sequences",
                    "state",
                    "confidence",
                ),
            )
            for item in session.vocabulary
        ]
        if finished
        else []
    )
    return result
