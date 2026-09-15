"""Exam mode patient: the speech model speaks freely, the application audits what it said.

`realtime_instructions` briefs the speech-to-speech model with the authored case.
`FactAttributor` maps each spoken patient sentence back to authored case sources so the
existing delivery and evaluation pipeline can credit facts the learner actually heard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import PatientResponseSchema
from ari.domain.models import ConversationSession, ExecutionRecord, MedicalCase


def _case_payload(case: MedicalCase) -> dict[str, object]:
    return {
        "language": case.language,
        "opening_statement": case.opening_statement,
        "communication_style": case.communication_style,
        "unknown_response": case.unknown_response,
        "out_of_scope_response": case.out_of_scope_response,
        "facts": [
            {
                "id": fact.id,
                "category": fact.category,
                "value": fact.value,
                "patient_phrase": fact.patient_phrase,
                "patient_phrase_variants": list(fact.patient_phrase_variants),
                "disclosure": fact.disclosure.value,
            }
            for fact in case.facts
        ],
    }


def realtime_instructions(prompt: VersionedPrompt, case: MedicalCase) -> str:
    payload = json.dumps(_case_payload(case), ensure_ascii=False)
    return f"{prompt.content}\n\nPatient file (JSON):\n{payload}"


@dataclass(frozen=True, slots=True)
class Attribution:
    selected_fact_ids: tuple[str, ...]
    execution: ExecutionRecord


class FactAttributor:
    def __init__(self, provider: LLMProvider, prompt: VersionedPrompt) -> None:
        self._provider = provider
        self._prompt = prompt

    async def attribute(
        self,
        *,
        session: ConversationSession,
        case: MedicalCase,
        patient_text: str,
        turn_id: str,
    ) -> Attribution:
        case_payload = _case_payload(case)
        case_payload["available_sources"] = [
            {"ref": "opening_statement", "value": case.opening_statement},
            *({"ref": f"fact:{fact.id}", "value": fact.value} for fact in case.facts),
        ]
        request = LLMRequest(
            messages=(
                {"role": "system", "content": self._prompt.content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "simulation_language": case.language,
                            "case": case_payload,
                            "patient_utterance": patient_text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ),
            context=ExecutionContext(
                session_id=session.id,
                turn_id=turn_id,
                learner_id=session.learner_id,
                operation="fact_attribution",
                case_version=case.version,
                case_hash=case.content_hash,
                prompt_version=self._prompt.version,
                prompt_hash=self._prompt.content_hash,
            ),
        )
        result = await self._provider.generate_structured(request, PatientResponseSchema)
        # Post-hoc audit: unknown references are dropped, never credited.
        selected = tuple(
            dict.fromkeys(
                fact_id
                for source_ref in result.value.source_refs
                for fact_id in (
                    (source_ref.removeprefix("fact:"),)
                    if source_ref.startswith("fact:")
                    else case.source_revealed_fact_ids.get(source_ref, ())
                )
                if fact_id in case.fact_ids
            )
        )
        return Attribution(selected, result.execution)
