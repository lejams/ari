from __future__ import annotations

import json
from dataclasses import dataclass

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import GroundingAuditOutputSchema
from ari.domain.models import (
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    GroundingAudit,
    MedicalCase,
    new_id,
)


@dataclass(frozen=True, slots=True)
class GroundingOutcome:
    audit: GroundingAudit
    execution: ExecutionRecord


class GroundingAuditor:
    def __init__(self, llm: LLMProvider, prompt: VersionedPrompt) -> None:
        self._llm = llm
        self._prompt = prompt

    async def audit(
        self, session: ConversationSession, case: MedicalCase, turn: ConversationTurn
    ) -> GroundingOutcome:
        payload = {
            "case": {
                "demographics": dict(case.demographics),
                "facts": [
                    {"id": fact.id, "category": fact.category, "value": fact.value}
                    for fact in case.facts
                ],
            },
            "patient_utterance": turn.patient_text,
            "interrupted": turn.interrupted,
        }
        context = ExecutionContext(
            session_id=session.id,
            learner_id=session.learner_id,
            turn_id=turn.id,
            operation="grounding_audit",
            case_version=case.version,
            case_hash=case.content_hash,
            prompt_version=self._prompt.version,
            prompt_hash=self._prompt.content_hash,
        )
        result = await self._llm.generate_structured(
            LLMRequest(
                messages=(
                    {"role": "system", "content": self._prompt.content},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ),
                context=context,
            ),
            GroundingAuditOutputSchema,
        )
        supported = tuple(
            dict.fromkeys(
                fact_id
                for claim in result.value.mentioned_claims
                for fact_id in claim.supported_fact_ids
                if fact_id in case.fact_ids
            )
        )
        audit = GroundingAudit(
            id=new_id(),
            session_id=session.id,
            turn_id=turn.id,
            schema_version="grounding-audit-v1",
            prompt_version=self._prompt.version,
            supported_fact_ids=supported,
            unsupported_claims=tuple(result.value.unsupported_claims),
            severity=result.value.severity,
            confidence=result.value.confidence,
        )
        return GroundingOutcome(audit=audit, execution=result.execution)
