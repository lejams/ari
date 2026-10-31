from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.prompting import load_prompt
from ari.application.schemas import EvaluationOutputSchema, PatientResponseSchema
from ari.application.services.assessment import weighted_assessment
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.patient import PatientSimulator
from ari.config import PROJECT_ROOT
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import (
    CEFRLevel,
    ConversationSession,
    Evaluation,
    ExecutionRecord,
    ExecutionStatus,
    SessionStatus,
    new_id,
)
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


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


def test_evaluation_schema_rejects_model_owned_numeric_criteria() -> None:
    with pytest.raises(ValidationError, match="criteria"):
        EvaluationOutputSchema.model_validate(
            {
                "summary": "Retour qualitatif.",
                "criteria": [
                    {
                        "criterion_id": "clinical_coverage",
                        "score": 5,
                        "evidence_turn_sequences": [1],
                        "feedback": "Non autorisé.",
                    }
                ],
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
        PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "patient_v3.txt",
        "patient-v3",
    )
    guarded = ConversationOrchestrator(
        container.repository,
        container.cases,
        PatientSimulator(InventingProvider(), prompt),
        LLMBackedEvaluator(
            FakeLLMProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v9.txt",
                "evaluation-v9",
            ),
            "fr-FR",
        ),
        container.voice_stack,
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


def test_wrong_language_response_cannot_reference_case_sources() -> None:
    with pytest.raises(ValidationError):
        PatientResponseSchema.model_validate(
            {"response_kind": "wrong_language", "source_refs": ["fact:symptom.location"]}
        )
    assert (
        PatientResponseSchema.model_validate({"response_kind": "wrong_language"}).source_refs == []
    )


class CodeSwitchInventingProvider(FakeLLMProvider):
    """Deterministic fake, except code switches point at a turn that never happened."""

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        result = await super().generate_structured(request, response_model)
        if response_model is EvaluationOutputSchema:
            payload = result.value.model_dump()
            payload["code_switches"] = [
                {"turn": 99, "fragment": "invented", "intended_term": None}
            ]
            return ProviderResult(response_model.model_validate(payload), result.execution)
        return result


class EmpathyInventingProvider(FakeLLMProvider):
    """Deterministic fake, except it judges a moment the application did not supply."""

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        result = await super().generate_structured(request, response_model)
        if response_model is EvaluationOutputSchema:
            payload = result.value.model_dump()
            payload["empathy"] = [
                {
                    "moment_id": "invented",
                    "verdict": "ignored",
                    "feedback": "Inventé.",
                    "evidence_turn_sequences": [1],
                }
            ]
            return ProviderResult(response_model.model_validate(payload), result.execution)
        return result


class QualitativeFeedbackProvider(FakeLLMProvider):
    """Changes only feedback, never application-owned assessment values."""

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        result = await super().generate_structured(request, response_model)
        if response_model is EvaluationOutputSchema:
            payload = result.value.model_dump()
            payload["summary"] = "Retour qualitatif différent."
            payload["priorities"] = [
                {"text": "Priorité qualitative différente.", "evidence_turn_sequences": [1]}
            ]
            return ProviderResult(response_model.model_validate(payload), result.execution)
        return result


@pytest.mark.asyncio
async def test_code_switch_on_unknown_turn_is_rejected_and_traced(container: Container) -> None:
    guarded = ConversationOrchestrator(
        container.repository,
        container.cases,
        PatientSimulator(
            FakeLLMProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "patient_v3.txt",
                "patient-v3",
            ),
        ),
        LLMBackedEvaluator(
            CodeSwitchInventingProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v9.txt",
                "evaluation-v9",
            ),
            "fr-FR",
        ),
        container.voice_stack,
    )
    learner = guarded.create_learner(CEFRLevel.C1)
    case = container.cases.list()[0]
    session = guarded.create_session(learner.id, case.id, case.version)
    guarded.activate(session.id)
    await guarded.process_transcript(session.id, "Seit wann haben Sie Schmerzen?")

    with pytest.raises(ProviderError, match="unknown transcript turn"):
        await guarded.end_session(session.id)

    persisted = container.repository.get_session(session.id)
    assert persisted.status is SessionStatus.ANALYSIS_FAILED
    assert any(e.error_code == "structured_output_guard" for e in persisted.executions)


@pytest.mark.asyncio
async def test_unknown_empathy_judgement_is_rejected(container: Container) -> None:
    guarded = ConversationOrchestrator(
        container.repository,
        container.cases,
        container.orchestrator._patient,
        LLMBackedEvaluator(
            EmpathyInventingProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v9.txt",
                "evaluation-v9",
            ),
            "fr-FR",
        ),
        container.voice_stack,
    )
    learner = guarded.create_learner(CEFRLevel.C1)
    case = container.cases.list()[0]
    session = guarded.create_session(learner.id, case.id, case.version)
    guarded.activate(session.id)
    await guarded.process_transcript(session.id, "Seit wann haben Sie Schmerzen?")

    with pytest.raises(ProviderError, match="Empathy judgements"):
        await guarded.end_session(session.id)


@pytest.mark.asyncio
async def test_qualitative_feedback_cannot_change_weighted_assessment(container: Container) -> None:
    case = container.cases.list()[0]

    async def analyse(provider: FakeLLMProvider) -> tuple[ConversationSession, Evaluation]:
        patient = PatientSimulator(
            FakeLLMProvider(
                {
                    PatientResponseSchema: lambda _: {
                        "response_kind": "sources",
                        "source_refs": ["fact:fact-1"],
                    }
                }
            ),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "patient_v3.txt",
                "patient-v3",
            ),
        )
        guarded = ConversationOrchestrator(
            container.repository,
            container.cases,
            patient,
            LLMBackedEvaluator(
                provider,
                load_prompt(
                    PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v9.txt",
                    "evaluation-v9",
                ),
                "fr-FR",
            ),
            container.voice_stack,
        )
        learner = guarded.create_learner(CEFRLevel.C1)
        session = guarded.create_session(learner.id, case.id, case.version)
        guarded.activate(session.id)
        first = await guarded.process_transcript(session.id, "Haben Sie Beschwerden?")
        repository = container.repository
        repository.begin_audio_stream(session.id, first.turn.id, "stream")
        repository.mark_audio_sent(session.id, first.turn.id, "stream", 1)
        repository.confirm_audio_started(
            session.id, first.turn.id, "stream", provider_response_id=None, last_index=0
        )
        repository.confirm_audio_delivered(
            session.id, first.turn.id, "stream", provider_response_id=None, last_index=0
        )
        completed = await guarded.end_session(session.id)
        assert completed.session.evaluation is not None
        return completed.session, completed.session.evaluation

    baseline_session, baseline = await analyse(FakeLLMProvider())
    changed_session, changed = await analyse(QualitativeFeedbackProvider())
    assert baseline.summary != changed.summary
    assert baseline.criteria == tuple(weighted_assessment(baseline_session, case))
    assert changed.criteria == tuple(weighted_assessment(changed_session, case))
    assert baseline.criteria == changed.criteria
    assert baseline.overall_score == changed.overall_score
    assert [criterion["criterion_id"] for criterion in baseline.criteria] == [
        criterion.id for criterion in case.rubric
    ]
    clinical_coverage = next(
        criterion
        for criterion in baseline.criteria
        if criterion["criterion_id"] == "clinical_coverage"
    )
    assert clinical_coverage["score"] == 1.25
    assert baseline.overall_score == 1.25
    maximum = float(sum(item.max_score for item in case.rubric))
    assert baseline.max_score == changed.max_score == maximum
