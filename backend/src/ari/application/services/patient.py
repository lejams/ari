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
        request = LLMRequest(
            messages=(
                {"role": "system", "content": self._prompt.content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "simulation_language": case.language,
                            "case": case_payload,
                            "previous_transcript": transcript,
                            "conversation_state": {"elapsed_seconds": elapsed_seconds},
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
                prompt_version=self._prompt.version,
                prompt_hash=self._prompt.content_hash,
            ),
        )
        result = await self._provider.generate_structured(request, PatientResponseSchema)
        allowed_refs = {
            "opening_statement",
            *(f"fact:{fact_id}" for fact_id in case.fact_ids),
        }
        unknown = set(result.value.source_refs) - allowed_refs
        if unknown:
            message = f"Patient response referenced unknown facts: {sorted(unknown)}"
            raise ProviderError(
                message,
                execution=replace(
                    result.execution,
                    status=ExecutionStatus.FAILED,
                    error_code="structured_output_guard",
                    error_message=message,
                ),
            )
        rendered_sources = {
            "opening_statement": case.opening_statement,
            **{f"fact:{fact.id}": fact.patient_phrase for fact in case.facts},
        }
        if result.value.response_kind == "sources":
            spoken_text = " ".join(
                rendered_sources[source_ref] for source_ref in result.value.source_refs
            )
        elif result.value.response_kind == "out_of_scope":
            spoken_text = case.out_of_scope_response
        else:
            spoken_text = case.unknown_response
        selected_fact_ids = tuple(
            dict.fromkeys(
                fact_id
                for source_ref in result.value.source_refs
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
            execution=result.execution,
            source_refs=tuple(result.value.source_refs),
        )
