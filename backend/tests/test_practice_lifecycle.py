from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from ari.application.services.practice import PracticeService
from ari.application.services.practice_lifecycle import (
    PatientVoiceWorkflow,
    PracticeLifecycle,
    StructuredTextWorkflow,
)
from ari.application.services.practice_projection import project_voice
from ari.application.services.realtime_fidelity import verify_response_fidelity
from ari.domain.errors import InvalidStateError
from ari.domain.models import ConversationTurn, InteractionMode, VoiceProfile, new_id, utc_now
from ari.domain.practice_lifecycle import (
    CanonicalResponse,
    NormalizedText,
    PracticeHandleKind,
    PracticeModality,
    PracticeTransport,
)
from ari.infrastructure.cases.practice_catalog import PublishedPracticeCatalog
from ari.infrastructure.cases.practice_demos import synthetic_demos
from ari.infrastructure.persistence.practice import SqlPracticeRepository


def test_common_contracts_and_realtime_fidelity() -> None:
    assert PracticeHandleKind.PATIENT_VOICE.value == "patient_voice"
    assert PracticeModality.VOICE.value == "voice"
    assert PracticeTransport.REALTIME.value == "realtime"
    assert verify_response_fidelity(" Hallo! ", "hallo").matches
    assert not verify_response_fidelity("one", "two").matches
    assert NormalizedText("x").schema_version == "normalized-text-v1"
    assert CanonicalResponse("x").schema_version == "canonical-response-v1"


def test_legacy_projection_does_not_backfill_new_fields(container: Any) -> None:
    learner = container.orchestrator.create_learner("C1")
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(
        learner.id, case.id, case.version,
        VoiceProfile.ECONOMY, InteractionMode.GUIDED,
    )
    old_turn = ConversationTurn(
        id=new_id(), session_id=session.id, sequence=1,
        user_text="Seit wann?", patient_text="Seit gestern.",
        revealed_fact_ids=(), created_at=utc_now(),
    )
    projected = project_voice(replace(session, turns=(old_turn,))).turns[0]
    assert projected.normalized_user_text is None
    assert projected.canonical_response is None
    assert projected.decisions == ({"provenance": "legacy"},)


def test_canonical_response_is_immutable_when_provider_id_is_attached(container: Any) -> None:
    learner = container.orchestrator.create_learner("C1")
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)
    turn = container.orchestrator.reserve_transcript(session.id, "Seit wann?")
    canonical = {"text": "Seit gestern.", "version": "canonical-response-v1"}
    selected = container.repository.save_selected_response(
        turn.id, patient_text="Seit gestern.", selected_fact_ids=(),
        provider_response_id=None, provider_response_status="completed",
        canonical_response=canonical, normalized_user_text="seit wann?",
    )
    assert selected.canonical_response == canonical
    with pytest.raises(InvalidStateError, match="Canonical response is immutable"):
        container.repository.save_selected_response(
            turn.id, patient_text="Seit gestern.", selected_fact_ids=(),
            provider_response_id="response-1", provider_response_status="completed",
            canonical_response={"text": "Andere Antwort"},
        )


def test_observed_mismatch_is_not_delivered_or_credited(container: Any) -> None:
    learner = container.orchestrator.create_learner("C1")
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)
    turn = container.orchestrator.reserve_transcript(session.id, "Seit wann?")
    container.repository.save_selected_response(
        turn.id, patient_text="Seit gestern.", selected_fact_ids=("fact-1",),
        provider_response_id="response-1", provider_response_status="completed",
        canonical_response={"text": "Seit gestern."},
    )
    failed = container.repository.record_observed_response(
        turn.id, "Une autre réponse", fidelity_matches=False
    )
    assert failed.provider_response_status == "canonical_response_mismatch"
    assert failed.delivery_status.value == "failed"
    assert failed.selected_fact_ids == ("fact-1",)
    assert failed.revealed_fact_ids == ()


def test_transport_finalize_mismatch_replay_is_terminal_and_strict(container: Any) -> None:
    learner = container.orchestrator.create_learner("C1")
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)
    turn = container.orchestrator.reserve_transcript(session.id, "Seit wann?")
    container.repository.save_selected_response(
        turn.id,
        patient_text="Seit gestern.",
        selected_fact_ids=("fact-1",),
        provider_response_id=None,
        provider_response_status="completed",
        canonical_response={"text": "Seit gestern."},
    )
    failed = container.repository.finalize_transport_response(
        turn.id,
        provider_response_id="response-1",
        provider_response_status="completed",
        canonical_response={"text": "Seit gestern."},
        observed_response_text="Autre réponse",
        fidelity_matches=False,
    )
    replay = container.repository.finalize_transport_response(
        turn.id,
        provider_response_id="response-1",
        provider_response_status="completed",
        canonical_response={"text": "Seit gestern."},
        observed_response_text="Autre réponse",
        fidelity_matches=False,
    )
    assert replay == failed
    with pytest.raises(InvalidStateError):
        container.repository.finalize_transport_response(
            turn.id,
            provider_response_id="response-1",
            provider_response_status="cancelled",
            canonical_response={"text": "Seit gestern."},
            observed_response_text="Autre réponse",
            fidelity_matches=False,
        )
    with pytest.raises(InvalidStateError):
        container.repository.finalize_transport_response(
            turn.id,
            provider_response_id="response-1",
            provider_response_status="completed",
            canonical_response={"text": "Depuis hier."},
            observed_response_text="Autre réponse",
            fidelity_matches=False,
        )


def test_transport_finalize_replay_preserves_audio_progress(container: Any) -> None:
    learner = container.orchestrator.create_learner("C1")
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)
    turn = container.orchestrator.reserve_transcript(session.id, "Seit wann?")
    selected = container.repository.finalize_transport_response(
        turn.id,
        provider_response_id="response-1",
        provider_response_status="completed",
        canonical_response={"text": "Seit gestern."},
        observed_response_text="Seit gestern.",
        fidelity_matches=True,
    )
    streaming = container.repository.begin_audio_stream(session.id, turn.id, "audio-1")
    sent = container.repository.mark_audio_sent(session.id, turn.id, "audio-1", 1)
    replay = container.repository.finalize_transport_response(
        turn.id,
        provider_response_id="response-1",
        provider_response_status="completed",
        canonical_response={"text": "Seit gestern."},
        observed_response_text="Seit gestern.",
        fidelity_matches=True,
    )
    assert selected.response_state.value == "response_selected"
    assert streaming.response_state.value == "audio_streaming"
    assert sent.response_state.value == "audio_sent"
    assert replay == sent


def test_transport_simulation_contains_no_case_facts(container: Any) -> None:
    case = container.cases.list()[0]
    spec = container.realtime_simulation.build_transport(case)
    assert case.title not in spec.instructions
    assert all(fact.value not in spec.instructions for fact in case.facts)
    assert all(str(value) not in spec.instructions for value in case.demographics.values())


async def test_lifecycle_submits_structured_and_voice_turns(container: Any) -> None:
    learner = container.orchestrator.create_learner("C1")
    practice_service = PracticeService(
        PublishedPracticeCatalog(container.cases.store, synthetic_demos()),
        SqlPracticeRepository(container.repository.engine, allow_synthetic=True),
    )
    lifecycle = PracticeLifecycle(
        StructuredTextWorkflow(practice_service),
        PatientVoiceWorkflow(container.orchestrator, container.repository),
    )
    content = practice_service.catalog.list()[0]
    text_handle = lifecycle.start_practice(
        PracticeHandleKind.STRUCTURED_TEXT,
        learner.id,
        scenario_id=content.scenario_id,
        scenario_version=content.scenario_version,
        mode="training",
        request_id=new_id(),
    )
    text_turn = await lifecycle.submit_turn(text_handle, "Seit einem Tag.")
    assert text_turn.modality is PracticeModality.TEXT
    voice_case = container.cases.list()[0]
    voice_handle = lifecycle.start_practice(
        PracticeHandleKind.PATIENT_VOICE,
        learner.id,
        case_id=voice_case.id,
        case_version=voice_case.version,
    )
    voice_turn = await lifecycle.submit_turn(voice_handle, "Seit wann?")
    assert voice_turn.modality is PracticeModality.VOICE
    assert voice_turn.canonical_response is not None
