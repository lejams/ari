from __future__ import annotations

from typing import cast

import pytest
import yaml

from ari.config import PROJECT_ROOT
from ari.domain.errors import CaseValidationError
from ari.domain.models import CaseMode, EducationalTarget
from ari.infrastructure.cases.loader import load_cases


def test_cases_are_versioned_and_have_unique_source_facts() -> None:
    catalog = load_cases(PROJECT_ROOT / "cases")
    assert len(catalog.list()) == 2
    for case in catalog.list():
        assert case.mode is CaseMode.FSP
        assert case.version
        assert len(case.content_hash) == 64
        assert len(case.fact_ids) == len(case.facts)


def test_user_case_is_loaded_without_losing_educational_metadata() -> None:
    case = load_cases(PROJECT_ROOT / "cases").get("ARI-FSP-001", "1.0")

    assert case.validation_status == "user_provided_test_unvalidated"
    assert case.content_hash == "cff0e30dd3d3999145e8b01d23425338443f31e09d4d692ab266ac96d6aabaea"
    assert case.difficulty == "beginner_intermediate"
    assert case.educational_target == EducationalTarget(
        exam="FSP", phase="Arzt-Patienten-Gespräch", duration_minutes=20
    )
    assert len(case.facts) == 44
    assert case.demographics["name"] == "Sabine Keller"
    assert case.source_revealed_fact_ids["opening_statement"] == ("symptom.location",)
    facts = {fact.id: fact for fact in case.facts}
    assert facts["symptom.meal_context"].value.endswith("Schnitzel und Pommes")
    assert facts["symptom.vomiting"].value.endswith("ohne Blut")
    assert facts["surgery.caesarean_sections"].value == "Kaiserschnitte 2002 und 2005"
    assert facts["allergy.food_none"].value == "Keine bekannten Lebensmittelallergien"
    assert facts["family.father"].value.endswith("an einem Herzinfarkt verstorben")
    assert facts["gynecology.last_menstrual_period"].value.endswith("zwei Jahren")
    assert facts["concern.hospitalization"].patient_phrase == (
        "Muss ich denn im Krankenhaus bleiben?"
    )

    mutable_demographics = cast(dict[str, object], case.demographics)
    with pytest.raises(TypeError):
        mutable_demographics["age"] = 99


def test_invalid_disclosure_rule_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    raw = yaml.safe_load((PROJECT_ROOT / "cases" / "ari_fsp_001.v1.yaml").read_text())
    raw["facts"][0]["disclosure"] = "whenever_the_model_wants"
    (tmp_path / "invalid.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(CaseValidationError, match="Invalid case file"):
        load_cases(tmp_path)


def test_technical_case_is_explicitly_enabled_and_preserves_fact_ids() -> None:
    catalog = load_cases(PROJECT_ROOT / "cases", include_technical_test=True)
    german = catalog.get("fsp-abdominal-pain", "1.2.0")
    english = catalog.get("technical-abdominal-pain-en", "1.1.0")

    assert german.language == "de-DE"
    assert english.language == "en-US"
    assert german.fact_ids == english.fact_ids

    translated = catalog.get("ARI-FSP-001-EN", "1.0")
    source = catalog.get("ARI-FSP-001", "1.0")
    assert translated.validation_status == "technical_translation_unvalidated"
    assert translated.fact_ids == source.fact_ids
    assert english.validation_status == "synthetic_technical_test"


def test_all_historical_case_hashes_are_stable() -> None:
    catalog = load_cases(PROJECT_ROOT / "cases", include_technical_test=True)
    hashes = {(case.id, case.version): case.content_hash for case in catalog.list()}

    assert hashes == {
        ("ARI-FSP-001", "1.0"): (
            "cff0e30dd3d3999145e8b01d23425338443f31e09d4d692ab266ac96d6aabaea"
        ),
        ("ARI-FSP-001-EN", "1.0"): (
            "908921aab12b8f832e5613d927451c8ddae33fd45a5b3a80c9936fbe9e6ef981"
        ),
        ("fsp-abdominal-pain", "1.2.0"): (
            "7308acdde84604dccb27b1ddcc9a49bbaefd2ac40d8387be832416f84e9b89e8"
        ),
        ("technical-abdominal-pain-en", "1.1.0"): (
            "c07d42d45ec530c049d508ecbb367629801bc394cc8c8fb1cb01de9462fd705e"
        ),
    }


@pytest.mark.parametrize(
    ("assessment_items", "message"),
    [
        (
            [
                {"id": "item", "label": "One", "satisfied_by_fact_ids": ["symptom.location"]},
                {"id": "item", "label": "Two", "satisfied_by_fact_ids": ["symptom.onset"]},
            ],
            "Duplicate assessment item id",
        ),
        (
            [{"id": "item", "label": "Unknown", "satisfied_by_fact_ids": ["unknown.fact"]}],
            "Unknown assessment fact",
        ),
        (
            [
                {
                    "id": "item",
                    "label": "Duplicate reference",
                    "satisfied_by_fact_ids": ["symptom.location", "symptom.location"],
                }
            ],
            "Duplicate assessment fact reference",
        ),
    ],
)
def test_assessment_item_references_are_validated(
    tmp_path,
    assessment_items: list[dict[str, object]],
    message: str,  # type: ignore[no-untyped-def]
) -> None:
    raw = yaml.safe_load((PROJECT_ROOT / "cases" / "ari_fsp_001.v1.yaml").read_text())
    raw["assessment_items"] = assessment_items
    (tmp_path / "invalid.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(CaseValidationError, match=message):
        load_cases(tmp_path)
