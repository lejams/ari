import json
from datetime import UTC, datetime

import pytest
from clinical_fixtures import synthetic_bundle
from practice_fixtures import synthetic_practice
from pydantic import ValidationError

from ari.domain.clinical import ClinicalBundle
from ari.domain.practice import PracticeAnswer, PracticeContent, assess_practice


def answer(index: int, text: str) -> PracticeAnswer:
    return PracticeAnswer(
        event_id=f"event-{index}",
        question_id=f"question-{index}",
        text=text,
        submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_adding_optional_practice_preserves_existing_golden_hashes() -> None:
    bundle = synthetic_bundle()
    assert "practice" not in bundle.scenarios[0].model_dump()
    assert bundle.content_hash == "e6cf66d942c15cca77a01a4caadc06200764a9d3b58feecac7dc2f6eef865d46"
    assert bundle.scenarios[0].content_hash == (
        "0224628ab2e66bb894ce7284f9b14549667bcbb7dfe34a91b762d9b23b101079"
    )


@pytest.mark.parametrize("phase", ["arzt_arzt", "fachbegriffe"])
def test_explicit_variants_evidence_and_weighted_dimensions(phase: str) -> None:
    content = synthetic_practice(phase)
    feedback = assess_practice(content, (answer(1, "  SEIT  1 TAG! "), answer(2, "nicht bekannt")))
    assert feedback.state == "provisional"  # A synthetic test is not human-validated.
    assert feedback.dimensions[0].score == pytest.approx(5 * 2 / 3)
    assert feedback.dimensions[0].answered_weight == 3
    assert feedback.items[0].evidence.submitted_text == "  SEIT  1 TAG! "
    assert feedback.items[0].evidence.accepted_variant == "Seit 1 Tag."
    assert feedback.items[1].state == "not_matched"
    assert "SYNTHETIC second response" in feedback.next_step
    assert "CEFR" in feedback.limitations
    assert "overall_score" not in feedback.model_dump()
    assert feedback.dimensions[0].id == ("lexical" if phase == "fachbegriffe" else "presentation")


def test_unknown_answers_not_zero_and_negated_answer_not_positive_evidence() -> None:
    content = synthetic_practice()
    empty = assess_practice(content, ())
    assert empty.state == "no_data" and empty.dimensions[0].score is None
    assert empty.dimensions[0].answered_weight == 0
    partial = assess_practice(content, (answer(1, "Nicht seit 1 Tag."),))
    assert partial.state == "provisional"
    assert partial.items[0].state == "not_matched"
    assert partial.items[1].state == "not_answered" and partial.items[1].evidence is None
    assert partial.dimensions[0].answered_weight == 2
    assert partial.dimensions[0].expected_weight == 3
    with pytest.raises(ValueError, match="seule réponse"):
        assess_practice(content, (answer(1, "Seit 1 Tag."), answer(1, "Seit 1 Tag.")))


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_fact",
        "unknown_term",
        "missing_item",
        "delivered_fact",
        "no_context",
        "wrong_phase",
        "extra_case",
    ],
)
def test_invalid_practice_refs_and_evidence_refused(mutation: str) -> None:
    raw = synthetic_practice().model_dump(mode="json")
    bundle = raw["bundle"]
    spec = bundle["scenarios"][0]["practice"]
    if mutation == "unknown_fact":
        spec["context_fact_ids"] = ["missing"]
    elif mutation == "unknown_term":
        spec["questions"][0]["term_id"] = "missing"
    elif mutation == "missing_item":
        spec["questions"] = spec["questions"][:1]
    elif mutation == "delivered_fact":
        spec["assessment_items"][0]["evidence_rule"] = "delivered_facts"
    elif mutation == "no_context":
        spec["context_fact_ids"] = []
    elif mutation == "wrong_phase":
        bundle["scenarios"][0]["phase"] = "arztbrief"
    elif mutation == "extra_case":
        second = {**bundle["cases"][0], "id": "unreferenced"}
        bundle["cases"].append(second)
    with pytest.raises(ValidationError):
        PracticeContent.model_validate_json(json.dumps(raw))


def test_questions_coaching_or_variant_changes_invalidate_content_hash() -> None:
    content = synthetic_practice()
    raw = content.bundle.model_dump(mode="json")
    raw["scenarios"][0]["practice"]["questions"][0]["coaching_fr"] = "Changed coaching"
    after = ClinicalBundle.model_validate_json(json.dumps(raw))
    assert after.scenarios[0].content_hash != content.bundle.scenarios[0].content_hash


def test_practice_reuses_patient_case_without_replacing_its_facts_or_criteria() -> None:
    patient_bundle = synthetic_bundle()
    practice = synthetic_practice()
    assert practice.bundle.cases[0].content_hash == patient_bundle.cases[0].content_hash
    assert practice.bundle.cases[0].assessment_items[0].evidence_kind == "delivered_facts"
    combined = patient_bundle.model_copy(
        update={
            "rubrics": patient_bundle.rubrics + practice.bundle.rubrics,
            "scenarios": (
                *patient_bundle.scenarios,
                practice.bundle.scenarios[0].model_copy(update={"id": "practice-scenario"}),
            ),
        }
    )
    assert ClinicalBundle.model_validate_json(combined.model_dump_json())


def test_rubric_method_must_match_practice_even_with_correct_hash() -> None:
    content = synthetic_practice()
    rubric = content.bundle.rubrics[0].model_copy(
        update={"scoring_version": "assessment-weighted-v1"}
    )
    scenario = content.bundle.scenarios[0].model_copy(update={"rubric_hash": rubric.content_hash})
    raw = content.bundle.model_copy(update={"rubrics": (rubric,), "scenarios": (scenario,)})
    with pytest.raises(ValidationError, match="Méthode de rubrique incompatible"):
        ClinicalBundle.model_validate_json(raw.model_dump_json())


def test_progression_separates_scenario_versions_even_when_answers_are_identical() -> None:
    from ari.application.services.progression import practice_progression
    from ari.domain.practice import PracticeRun

    runs = []
    for version in ("1", "2"):
        content = synthetic_practice()
        scenario = content.bundle.scenarios[0].model_copy(update={"version": version})
        bundle = ClinicalBundle.model_validate_json(
            content.bundle.model_copy(update={"scenarios": (scenario,)}).model_dump_json(),
        )
        content = PracticeContent(
            bundle=bundle,
            scenario_id=scenario.id,
            scenario_version=version,
            provenance="synthetic_demo",
        )
        answers = (answer(1, "Seit 1 Tag."), answer(2, "Das weiß ich nicht."))
        runs.append(
            PracticeRun(
                id=f"run-{version}",
                learner_id="synthetic-owner",
                request_id=f"request-{version}",
                content=content,
                mode="exam",
                status="completed",
                answers=answers,
                feedback=assess_practice(content, answers),
                created_at=answers[0].submitted_at,
                ended_at=answers[-1].submitted_at,
            )
        )
    groups = practice_progression(tuple(runs))["groups"]
    assert len(groups) == 2
    assert {g["content"]["scenario_version"] for g in groups} == {"1", "2"}
