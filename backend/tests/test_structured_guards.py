from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.prompting import load_prompt
from ari.application.schemas import PatientResponseSchema
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.patient import PatientSimulator
from ari.application.voice_stacks import VoiceTransport
from ari.config import PROJECT_ROOT
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import CEFRLevel, ExecutionRecord, ExecutionStatus, new_id
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)


def test_patient_schema_cannot_return_unbound_spoken_text() -> None:
    with pytest.raises(ValidationError):
        PatientResponseSchema.model_validate(
            {
                "response_kind": "unknown",
                "source_refs": [],
                "spoken_text": "I also have diabetes.",
            }
        )


def test_out_of_scope_response_cannot_smuggle_case_sources() -> None:
    with pytest.raises(ValidationError):
        PatientResponseSchema.model_validate(
            {
                "response_kind": "out_of_scope",
                "source_refs": ["fact:symptom.location"],
            }
        )


class InventingProvider:
    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        assert response_model is PatientResponseSchema
        value = response_model.model_validate(
            {
                "response_kind": "sources",
                "source_refs": ["fact:invented.diabetes"],
            }
        )
        context = request.context
        execution = ExecutionRecord(
            id=new_id(),
            session_id=context.session_id,
            turn_id=context.turn_id,
            operation=context.operation,
            provider="malicious-test",
            model="inventor",
            status=ExecutionStatus.SUCCEEDED,
            prompt_version=context.prompt_version,
            prompt_hash=context.prompt_hash,
            case_version=context.case_version,
            case_hash=context.case_hash,
            latency_ms=1,
            usage={},
        )
        return ProviderResult(value, execution)


@pytest.mark.asyncio
async def test_invented_case_fact_is_rejected_and_traced(container: Container) -> None:
    prompt = load_prompt(
        PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "patient_v2.txt",
        "patient-v2",
    )
    guarded = ConversationOrchestrator(
        container.repository,
        container.cases,
        PatientSimulator(InventingProvider(), prompt),
        LLMBackedEvaluator(
            FakeLLMProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v1.txt",
                "evaluation-v1",
            ),
            "fr-FR",
        ),
        container.voice_stacks,
        VoiceTransport.PIPELINE,
    )
    learner = guarded.create_learner(CEFRLevel.C1)
    case = container.cases.list()[0]
    session = guarded.create_session(learner.id, case.id, case.version)
    guarded.activate(session.id)

    with pytest.raises(ProviderError):
        await guarded.process_transcript(session.id, "Haben Sie Diabetes?")

    persisted = container.repository.get_session(session.id)
    assert len(persisted.turns) == 1
    assert persisted.turns[0].user_text == "Haben Sie Diabetes?"
    assert persisted.turns[0].patient_text == ""
    assert persisted.turns[0].provider_response_status == "failed"
    assert len(persisted.executions) == 1
    assert persisted.executions[0].status is ExecutionStatus.FAILED
    assert persisted.executions[0].error_code == "structured_output_guard"
