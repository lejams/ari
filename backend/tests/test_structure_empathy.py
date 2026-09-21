"""Anamnesis sections, empathy moments and next actions: deterministic where possible."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from clinical_fixtures import synthetic_bundle
from conftest import build_test_container, publish_with_simulated_reviews
from pydantic import ValidationError

from ari.application.services.evaluation import empathy_triggers
from ari.application.services.next_actions import next_actions
from ari.application.services.structure import section_coverage
from ari.container import Container
from ari.domain.clinical import AnamnesisSection, ClinicalBundle, EmpathyMoment
from ari.domain.models import (
    AnamnesisSectionSpec,
    AudioDeliveryStatus,
    CEFRLevel,
    ConversationSession,
    ConversationTurn,
    EmpathyMomentSpec,
    LearningGoal,
    MedicalCase,
)

SECTIONS = (
    AnamnesisSection(id="aktuelle_beschwerden", label_de="Beschwerden", fact_ids=("fact-1",)),
    AnamnesisSection(id="patientendaten", label_de="Patientendaten", fact_ids=("fact-2",)),
)
MOMENT = EmpathyMoment(
    id="loss",
    fact_id="fact-2",
    cue_fr="Le patient évoque un deuil",
    expected_fr="Reconnaître le deuil avant de poursuivre",
)


def pedagogical_bundle(version: str = "1") -> ClinicalBundle:
    bundle = synthetic_bundle(version)
    scenario = bundle.scenarios[0].model_copy(
        update={"anamnesis_sections": SECTIONS, "empathy_moments": (MOMENT,)}
    )
    return ClinicalBundle.model_validate_json(
        bundle.model_copy(update={"scenarios": (scenario,)}).model_dump_json()
    )


def _turn(
    session_id: str, sequence: int, text: str, facts: tuple[str, ...] = ()
) -> ConversationTurn:
    return ConversationTurn(
        id=f"turn-{sequence}",
        session_id=session_id,
        sequence=sequence,
        user_text=text,
        patient_text="…",
        revealed_fact_ids=facts,
        delivery_status=AudioDeliveryStatus.DELIVERED if facts else AudioDeliveryStatus.FAILED,
        provider_response_status="completed",
    )


def _case(base: MedicalCase) -> MedicalCase:
    return replace(
        base,
        anamnesis_sections=tuple(
            AnamnesisSectionSpec(id=s.id, label=s.label_de, fact_ids=s.fact_ids) for s in SECTIONS
        ),
        empathy_moments=(
            EmpathyMomentSpec(MOMENT.id, MOMENT.fact_id, MOMENT.cue_fr, MOMENT.expected_fr),
        ),
    )


def test_legacy_scenarios_serialize_without_the_new_keys() -> None:
    plain = synthetic_bundle().scenarios[0]
    payload = json.loads(plain.model_dump_json())
    assert "empathy_moments" not in payload and "anamnesis_sections" not in payload
    enriched = pedagogical_bundle().scenarios[0]
    assert enriched.content_hash != plain.content_hash
    assert "anamnesis_sections" in json.loads(enriched.model_dump_json())


def test_bundle_rejects_unknown_facts_and_facts_in_two_sections() -> None:
    bundle = synthetic_bundle()
    bad_moment = bundle.scenarios[0].model_copy(
        update={"empathy_moments": (MOMENT.model_copy(update={"fact_id": "ghost"}),)}
    )
    with pytest.raises(ValidationError, match="moment d'empathie"):
        ClinicalBundle.model_validate_json(
            bundle.model_copy(update={"scenarios": (bad_moment,)}).model_dump_json()
        )
    duplicate = (
        SECTIONS[0],
        AnamnesisSection(id="noxen", label_de="Noxen", fact_ids=("fact-1",)),
    )
    with pytest.raises(ValidationError, match="sections d'anamnèse"):
        bundle.scenarios[0].model_copy(
            update={"anamnesis_sections": duplicate}
        ).model_validate_json(
            bundle.scenarios[0]
            .model_copy(update={"anamnesis_sections": duplicate})
            .model_dump_json()
        )


def test_section_coverage_counts_delivered_facts_and_order(published_container: Container) -> None:
    case = _case(published_container.cases.list()[0])
    session = ConversationSession(
        id="s",
        learner_id="l",
        case_id=case.id,
        case_version=case.version,
        case_hash="h",
        goal=LearningGoal(),
    )
    assert section_coverage(session, replace(case, anamnesis_sections=())) is None
    empty = section_coverage(session, case)
    assert empty is not None and empty["covered_count"] == 0 and empty["order_observed"] == []
    assert empty["canonical_order_respected"] is True

    # Beschwerden (canonical position 2) heard before Patientendaten (position 1).
    session = replace(
        session,
        turns=(
            _turn("s", 1, "Was ist mit fact-1?", ("fact-1",)),
            _turn("s", 2, "Wie heißen Sie?", ("fact-2",)),
            _turn("s", 3, "Danke."),
        ),
    )
    coverage = section_coverage(session, case)
    assert coverage is not None
    assert coverage["covered_count"] == 2 and coverage["total_count"] == 2
    assert coverage["order_observed"] == ["aktuelle_beschwerden", "patientendaten"]
    assert coverage["canonical_order_respected"] is False
    assert coverage["sections"][1]["first_turn"] == 2


def test_empathy_trigger_is_the_first_delivery_and_the_next_learner_turn(
    published_container: Container,
) -> None:
    case = _case(published_container.cases.list()[0])
    base = ConversationSession(
        id="s",
        learner_id="l",
        case_id=case.id,
        case_version=case.version,
        case_hash="h",
        goal=LearningGoal(),
    )
    not_triggered = empathy_triggers(replace(base, turns=(_turn("s", 1, "Hallo"),)), case)
    assert not_triggered[0]["verdict"] == "not_triggered"
    last_turn = replace(base, turns=(_turn("s", 1, "Familie?", ("fact-2",)),))
    assert empathy_triggers(last_turn, case)[0]["verdict"] == "not_reached"
    reachable = replace(
        base,
        turns=(_turn("s", 1, "Familie?", ("fact-2",)), _turn("s", 2, "Das tut mir leid.")),
    )
    moment = empathy_triggers(reachable, case)[0]
    assert (moment["trigger_turn"], moment["response_turn"], moment["verdict"]) == (1, 2, "pending")


def test_next_actions_follow_the_priority_order_and_cap_at_three() -> None:
    case = MedicalCase(
        id="c",
        version="1",
        validation_status="published",
        content_hash="h",
        language="de-DE",
        transcription_context="",
        unknown_response="",
        out_of_scope_response="",
        title="t",
        public_summary="",
        difficulty="",
        educational_target=None,  # type: ignore[arg-type]
        source_revealed_fact_ids={},
        opening_statement="",
        communication_style="",
        facts=(),
        rubric_version="r",
        rubric=(),
    )
    structure = {
        "sections": [
            {"id": "noxen", "label": "Noxen", "missing_fact_ids": ["smoking"]},
            {"id": "allergien", "label": "Allergien", "missing_fact_ids": []},
        ]
    }
    empathy = (
        {
            "moment_id": "loss",
            "verdict": "ignored",
            "response_turn": 4,
            "cue": "deuil",
            "expected": "reconnaître",
        },
    )
    actions = next_actions(
        case,
        [{"missing_required_item_ids": ["pain-history"]}],
        structure,
        empathy,
        lexicon_candidates=3,
    )
    assert [a["kind"] for a in actions] == ["missing_required_item", "uncovered_section", "empathy"]
    assert "pain-history" in actions[0]["text"] and "Noxen" in actions[1]["text"]
    assert "tour 4" in actions[2]["text"]
    only_words = next_actions(case, [], None, (), lexicon_candidates=1)
    assert [a["kind"] for a in only_words] == ["review_lexicon"] and "1 mot " in only_words[0][
        "text"
    ]
    assert next_actions(case, [], None, (), 0) == ()


@pytest.mark.asyncio
async def test_evaluation_judges_empathy_and_reports_structure(database_url: str) -> None:
    container = build_test_container(database_url)
    publish_with_simulated_reviews(container.cases.store, pedagogical_bundle())
    case = container.cases.list()[0]
    assert [s.id for s in case.anamnesis_sections] == ["aktuelle_beschwerden", "patientendaten"]
    assert case.empathy_moments[0].cue == MOMENT.cue_fr
    learner = container.orchestrator.create_learner(CEFRLevel.B1)

    async def run(second_turn: str) -> ConversationSession:
        session = container.orchestrator.create_session(learner.id, case.id, case.version)
        container.orchestrator.activate(session.id)
        first = await container.orchestrator.process_transcript(session.id, "Was ist mit fact-2?")
        repo = container.repository
        repo.begin_audio_stream(session.id, first.turn.id, "stream")
        repo.mark_audio_sent(session.id, first.turn.id, "stream", 1)
        repo.confirm_audio_started(
            session.id, first.turn.id, "stream", provider_response_id=None, last_index=0
        )
        repo.confirm_audio_delivered(
            session.id, first.turn.id, "stream", provider_response_id=None, last_index=0
        )
        await container.orchestrator.process_transcript(session.id, second_turn)
        return (await container.orchestrator.end_session(session.id)).session

    ignored = await run("Haben Sie Fieber?")
    assert ignored.evaluation is not None
    assert ignored.evaluation.schema_version == "session-evaluation-v4"
    moment = ignored.evaluation.empathy[0]
    assert (moment["verdict"], moment["trigger_turn"], moment["response_turn"]) == ("ignored", 1, 2)
    assert moment["evidence_turn_sequences"] == [2]
    structure = ignored.evaluation.structure
    assert structure is not None and structure["covered_count"] == 1
    assert structure["sections"][1]["covered_fact_ids"] == ["fact-2"]
    kinds = [a["kind"] for a in ignored.evaluation.next_actions]
    assert kinds == ["missing_required_item", "uncovered_section", "empathy"]

    acknowledged = await run("Das tut mir sehr leid. Seit wann haben Sie Schmerzen?")
    assert acknowledged.evaluation is not None
    assert acknowledged.evaluation.empathy[0]["verdict"] == "acknowledged"
    assert "empathy" not in [a["kind"] for a in acknowledged.evaluation.next_actions]
    # Reloaded from SQLite, the new fields survive the JSON round trip.
    reloaded = container.repository.get_session(acknowledged.id)
    assert reloaded.evaluation is not None
    assert reloaded.evaluation.structure == acknowledged.evaluation.structure
    assert reloaded.evaluation.empathy == acknowledged.evaluation.empathy
