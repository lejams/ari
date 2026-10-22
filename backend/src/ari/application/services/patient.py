import json
from dataclasses import dataclass, replace

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import PatientResponseSchema
from ari.domain.errors import ProviderError
from ari.domain.models import (
    ConversationSession,
    ExecutionRecord,
    ExecutionStatus,
    MedicalCase,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class PatientOutcome:
    spoken_text: str
    selected_fact_ids: tuple[str, ...]
    execution: ExecutionRecord
    source_refs: tuple[str, ...] = ()
    response_kind: str = "sources"


class PatientSimulator:
    def __init__(self, provider: LLMProvider, prompt: VersionedPrompt) -> None:
        self._provider = provider
        self._prompt = prompt

    async def respond(
        self,
        *,
        session: ConversationSession,
        case: MedicalCase,
        user_text: str,
        turn_id: str,
    ) -> PatientOutcome:
        request = patient_request(self._prompt, session, case, user_text, turn_id)
        result = await self._provider.generate_structured(request, PatientResponseSchema)
        guard_source_refs(case, result.value, result.execution)
        return render_outcome(case, result.value, result.execution)


def patient_request(
    prompt: VersionedPrompt,
    session: ConversationSession,
    case: MedicalCase,
    user_text: str,
    turn_id: str,
    extra: dict[str, object] | None = None,
) -> LLMRequest:
    """The patient's structured request: the authored case, the transcript, the question."""
    transcript = [
        {
            "turn": turn.sequence,
            "doctor": turn.user_text,
            "patient": turn.patient_text,
            "revealed_fact_ids": list(turn.revealed_fact_ids),
        }
        for turn in session.turns
    ]
    elapsed_seconds = (
        max(0, int((utc_now() - session.call_started_at).total_seconds()))
        if session.call_started_at is not None
        else 0
    )
    available_sources = [
        {"ref": "opening_statement", "value": case.opening_statement},
        *({"ref": f"fact:{fact.id}", "value": fact.value} for fact in case.facts),
    ]
    case_payload = {
        "language": case.language,
        "opening_statement": case.opening_statement,
        "communication_style": case.communication_style,
        "facts": [
            {
                "id": fact.id,
                "category": fact.category,
                "value": fact.value,
                "patient_phrase": fact.patient_phrase,
                "disclosure": fact.disclosure.value,
            }
            for fact in case.facts
        ],
        "available_sources": available_sources,
    }
    return LLMRequest(
        messages=(
            {"role": "system", "content": prompt.content},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "simulation_language": case.language,
                        "case": case_payload,
                        "previous_transcript": transcript,
                        "conversation_state": {"elapsed_seconds": elapsed_seconds},
                        **(extra or {}),
                        "doctor_latest_utterance": user_text,
                    },
                    ensure_ascii=False,
                ),
            },
        ),
        context=ExecutionContext(
            session_id=session.id,
            turn_id=turn_id,
            learner_id=session.learner_id,
            operation="patient_simulation",
            case_version=case.version,
            case_hash=case.content_hash,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
        ),
    )


def guard_source_refs(
    case: MedicalCase, value: PatientResponseSchema, execution: ExecutionRecord
) -> None:
    """A reference outside the authored case fails the call; it is never rendered."""
    allowed_refs = {
        "opening_statement",
        *(f"fact:{fact_id}" for fact_id in case.fact_ids),
    }
    unknown = set(value.source_refs) - allowed_refs
    if unknown:
        message = f"Patient response referenced unknown facts: {sorted(unknown)}"
        raise ProviderError(
            message,
            execution=replace(
                execution,
                status=ExecutionStatus.FAILED,
                error_code="structured_output_guard",
                error_message=message,
            ),
        )


def render_outcome(
    case: MedicalCase, value: PatientResponseSchema, execution: ExecutionRecord
) -> PatientOutcome:
    """The code decides the wording: authored phrases for the selected sources."""
    rendered_sources = {
        "opening_statement": case.opening_statement,
        **{f"fact:{fact.id}": fact.patient_phrase for fact in case.facts},
    }
    if value.response_kind == "sources":
        spoken_text = " ".join(rendered_sources[source_ref] for source_ref in value.source_refs)
    elif value.response_kind in {"out_of_scope", "wrong_language"}:
        # A patient who did not understand the language answers like an off-topic
        # request: the authored phrase, no fact, and the turn keeps the kind as evidence.
        spoken_text = case.out_of_scope_response
    else:
        spoken_text = case.unknown_response
    selected_fact_ids = tuple(
        dict.fromkeys(
            fact_id
            for source_ref in value.source_refs
            for fact_id in (
                (source_ref.removeprefix("fact:"),)
                if source_ref.startswith("fact:")
                else case.source_revealed_fact_ids.get(source_ref, ())
            )
        )
    )
    return PatientOutcome(
        spoken_text=spoken_text,
        selected_fact_ids=selected_fact_ids,
        execution=execution,
        source_refs=tuple(value.source_refs),
        response_kind=value.response_kind,
    )
