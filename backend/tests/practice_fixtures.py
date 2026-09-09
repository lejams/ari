"""Isolated synthetic practice assets. Never real reviews or Freiburg content."""

from clinical_fixtures import synthetic_bundle

from ari.domain.clinical import (
    ClinicalBundle,
    PracticeCriterion,
    PracticeQuestion,
    PracticeSpecification,
    RubricDimension,
    RubricVersion,
    VersionRef,
)
from ari.domain.practice import PracticeContent


def synthetic_practice(phase: str = "arzt_arzt") -> PracticeContent:
    bundle = synthetic_bundle()
    dimension = "lexical" if phase == "fachbegriffe" else "presentation"
    rubric = RubricVersion(
        id="synthetic-practice-rubric",
        version="1",
        scoring_version="practice-exact-answer-v1",
        dimensions=(
            RubricDimension(
                id=dimension,
                label=dimension,
                max_score=5,
                description="SYNTHETIC text matching, not clinical validity",
            ),
        ),
    )
    case = bundle.cases[0]
    assessment_items = (
        PracticeCriterion(
            id="test-one",
            dimension=dimension,
            weight=2.0,
            expected_behavior="SYNTHETIC first response",
            accepted_answers=("Seit einem Tag.", "Seit 1 Tag."),
        ),
        PracticeCriterion(
            id="test-two",
            dimension=dimension,
            weight=1.0,
            expected_behavior="SYNTHETIC second response",
            accepted_answers=("Das weiß ich nicht.",),
        ),
    )
    questions = tuple(
        PracticeQuestion(
            id=f"question-{i}",
            kind="term_definition"
            if phase == "fachbegriffe"
            else ("presentation" if i == 1 else "followup"),
            prompt_de=f"SYNTHETIC Frage {i}",
            coaching_fr=f"Aide de test {i}",
            assessment_item_id=item.id,
            term_id="symptom" if phase == "fachbegriffe" else None,
        )
        for i, item in enumerate(assessment_items, 1)
    )
    scenario = bundle.scenarios[0].model_copy(
        update={
            "phase": phase,
            "case_hash": case.content_hash,
            "rubric": VersionRef(id=rubric.id, version=rubric.version),
            "rubric_hash": rubric.content_hash,
            "practice": PracticeSpecification(
                context_fact_ids=("fact-1",), questions=questions, assessment_items=assessment_items
            ),
        }
    )
    # References rebuilt through strict validation, never a model_copy validation bypass.
    raw = bundle.model_dump(mode="json")
    raw.update(
        cases=[case.model_dump(mode="json")],
        rubrics=[rubric.model_dump(mode="json")],
        scenarios=[scenario.model_dump(mode="json")],
    )
    from json import dumps

    validated = ClinicalBundle.model_validate_json(dumps(raw))
    return PracticeContent(
        bundle=validated,
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        provenance="synthetic_demo",
    )
