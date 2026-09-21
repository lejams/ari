"""Protocol records: strict contract, deterministic blockers, gold freezing, PII detection."""

from __future__ import annotations

import json

import pytest
from content_fixtures import settled_record, synthetic_gold, synthetic_record
from pydantic import ValidationError

from ari.content.domain.pii import scan
from ari.content.domain.protocol import (
    AnamnesisItem,
    GoldProtocol,
    Pitfall,
    ProtocolRecord,
)


def test_hash_is_canonical_and_round_trips_through_json() -> None:
    record = synthetic_record()
    again = ProtocolRecord.model_validate_json(json.dumps(record.model_dump(mode="json")))
    assert again.content_hash == record.content_hash
    assert record.model_dump(mode="json")["location"]["land"] == "Bayern"


def test_unknown_value_and_pitfall_references_are_checked() -> None:
    with pytest.raises(ValidationError, match="polarity: unknown"):
        AnamnesisItem(
            id="x", section="noxen", label_de="Rauchen", value_de=None, polarity="present"
        )
    record = synthetic_record()
    raw = record.model_dump(mode="json")
    raw["pedagogy"]["pitfalls"] = [
        Pitfall(text_fr="?", related_item_ids=("nope",)).model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="inconnu du protocole"):
        ProtocolRecord.model_validate_json(json.dumps(raw))
    raw = record.model_dump(mode="json")
    raw["patient"]["summary_de"] = "Rückfragen an +49 170 1234567"
    with pytest.raises(ValidationError, match="Coordonnées"):
        ProtocolRecord.model_validate_json(json.dumps(raw))


def test_blockers_are_deterministic_per_stage() -> None:
    draft = synthetic_record()
    doctor = draft.review_blockers("doctor")
    assert any("anamnesis.a02" in b for b in doctor)
    assert any("outcome.result" in b for b in doctor)
    assert "Question critique sans réponse: u01" in doctor
    assert "Difficulté non renseignée" in doctor
    assert "Land manquant" not in doctor
    raw = draft.model_dump(mode="json")
    raw["location"]["land"] = None
    nowhere = ProtocolRecord.model_validate_json(json.dumps(raw))
    assert "Land manquant" in nowhere.review_blockers("owner")
    assert "Land manquant" not in nowhere.review_blockers("doctor")
    settled = settled_record()
    assert settled.review_blockers("doctor") == () and settled.review_blockers("owner") == ()


def _gold_with(**changes: object) -> GoldProtocol:
    raw = {**synthetic_gold().model_dump(mode="json"), **changes}
    return GoldProtocol.model_validate_json(json.dumps(raw))


def test_gold_requires_a_settled_located_record_with_matching_hash() -> None:
    gold = synthetic_gold()
    assert gold.content_hash != gold.protocol_hash  # gold_hash covers location, rights, actor
    draft = synthetic_record()
    with pytest.raises(ValidationError, match="aucune incertitude"):
        _gold_with(record=draft.model_dump(mode="json"), protocol_hash=draft.content_hash)
    with pytest.raises(ValidationError, match="hash du protocole"):
        _gold_with(protocol_hash="0" * 64)
    with pytest.raises(ValidationError, match="incompatibles"):
        _gold_with(rights="incompatible")


def test_pii_scan_masks_excerpts_and_names_the_path() -> None:
    findings = scan(
        {
            "patient": {"summary_de": "Frau Müller, geboren am 12.03.1971, Klinikum Augsburg"},
            "notes": ["Berliner Straße 12, 80331 München", "kein Problem"],
        }
    )
    kinds = {(f.kind, f.path) for f in findings}
    assert ("person", "patient.summary_de") in kinds
    assert ("exact_date", "patient.summary_de") in kinds
    assert ("institution", "patient.summary_de") in kinds
    assert ("address", "notes[0]") in kinds and ("postcode_city", "notes[0]") in kinds
    assert all("Müller" not in f.excerpt and "1971" not in f.excerpt for f in findings)
    assert scan(settled_record().model_dump(mode="json")) == ()
