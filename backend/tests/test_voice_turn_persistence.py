"""Persistence invariants for voice turns: canonical response, fidelity and audio progress."""

from __future__ import annotations

from typing import Any

import pytest

from ari.domain.errors import InvalidStateError


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
