import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from freiburg_fixtures import synthetic_freiburg, synthetic_workbook
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from ari.config import PROJECT_ROOT
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.infrastructure.cases.cli import run
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.freiburg_format import FreiburgExport
from ari.infrastructure.cases.freiburg_import import (
    FreiburgDraftStore,
    case_blockers,
    load_freiburg,
    material_hash,
    review_material,
)
from ari.infrastructure.cases.freiburg_workbook import verify_workbook
from ari.infrastructure.persistence.clinical_rows import DraftBatchRow, DraftCaseRow
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository


def test_missing_global_page_is_preserved_and_flagged_and_cross_case_page_rejected() -> None:
    raw = synthetic_freiburg().model_dump(mode="json")
    raw["open_questions"].append(
        {
            "question_id": "GLOBAL-TEST",
            "case_id": None,
            "category": "assessment",
            "question_fr": "TEST poids provisoires",
            "source_pages": [],
            "needs_review": True,
        }
    )
    export = FreiburgExport.model_validate_json(json.dumps(raw))
    assert export.open_questions[-1].source_pages == ()
    assert any(
        "GLOBAL-TEST: page source absente" in b for b in case_blockers(export, export.cases[0])
    )
    raw["open_questions"][0]["source_pages"] = [2]
    with pytest.raises(ValidationError, match="page hors cas"):
        FreiburgExport.model_validate_json(json.dumps(raw))


@pytest.fixture
def draft_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FreiburgDraftStore:
    url = f"sqlite:///{tmp_path / 'intake.db'}"
    monkeypatch.setenv("ARI_DATABASE_URL", url)
    cfg = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(cfg, "20260904_0004")
    command.upgrade(cfg, "head")
    command.check(cfg)
    return FreiburgDraftStore(SqliteSessionRepository(url).engine)


def test_real_shape_preserved_without_certainty_or_executable_defaults() -> None:
    export = synthetic_freiburg()
    material = review_material(export, export.cases[0])
    assert material["case"] == export.cases[0].model_dump(mode="json")
    assert material["case"]["facts"][0]["polarity"] == "unknown"
    assert material["case"]["facts"][0]["value_de"] == "kein Kontakt"
    assert material["case"]["demographics"]["children"] is None
    assert material["case"]["opening_line_de"] is None
    assert material["case"]["assessment_items"][1]["expected_answer_de"] is None
    assert material["case"]["assessment_items"][1]["satisfied_by_fact_ids"] == []
    assert material["weights_provisional"] is True
    assert material["rights"] == "unknown" and material["original_verified"] is False
    assert "GPLv3 [Frei]" in material["sources"][0]["license"]
    assert material["sources"][0]["checksum"] is None
    assert material["executable"] is False
    reformatted = FreiburgExport.model_validate_json(export.model_dump_json(indent=4))
    assert material_hash(material) == material_hash(
        review_material(reformatted, reformatted.cases[0])
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "unknown_ref",
        "cross_source",
        "bad_page",
        "bad_unit",
        "missing_known",
        "unknown_numeric",
        "published",
        "clinical_approved",
        "extra_field",
        "terms",
        "questions",
        "doctor_fact",
        "negative_weight",
        "bool_numeric",
        "bad_dose",
    ],
)
def test_transport_validation_is_fail_closed(mutation: str) -> None:
    raw = synthetic_freiburg().model_dump(mode="json")
    case, fact = raw["cases"][0], raw["cases"][0]["facts"][0]
    if mutation == "duplicate":
        case["facts"].append(fact)
    elif mutation == "unknown_ref":
        case["assessment_items"][0]["satisfied_by_fact_ids"] = ["unknown"]
    elif mutation == "cross_source":
        case["source_document_id"] = "unknown"
    elif mutation == "bad_page":
        fact["source_pages"] = [0]
    elif mutation == "bad_unit":
        fact["unit"] = "invented"
    elif mutation == "missing_known":
        fact.update(polarity="absent", value_de=None)
    elif mutation == "unknown_numeric":
        fact.update(numeric_value=3, unit="kg")
    elif mutation == "published":
        case["status"] = "published"
    elif mutation == "clinical_approved":
        raw["validation"]["medical"] = True
    elif mutation == "extra_field":
        case["automatic_approval"] = True
    elif mutation == "terms":
        case["terminology"] = []
    elif mutation == "questions":
        case["open_questions"] = []
    elif mutation == "doctor_fact":
        case["assessment_items"][1]["satisfied_by_fact_ids"] = [fact["fact_id"]]
    elif mutation == "negative_weight":
        case["assessment_items"][0]["weight"] = -1
    elif mutation == "bool_numeric":
        case["demographics"]["age_years"] = True
    elif mutation == "bad_dose":
        fact["medication"] = {
            "name": "TEST",
            "dose": None,
            "unit": None,
            "schedule": None,
            "indication_reported": None,
            "dose_unknown": False,
        }
    with pytest.raises(ValidationError):
        FreiburgExport.model_validate_json(json.dumps(raw))


def test_reconciliation_and_duplicate_json_keys(tmp_path: Path) -> None:
    export = synthetic_freiburg()
    workbook = synthetic_workbook(export)
    assert verify_workbook(export, workbook)["Facts"] == 1
    altered = export.model_dump(mode="json")
    altered["cases"][0]["demographics"]["age_years"] = 40
    with pytest.raises(ValueError, match="Écart JSON/XLSX"):
        verify_workbook(FreiburgExport.model_validate_json(json.dumps(altered)), workbook)
    with pytest.raises(ValueError, match="Formule"):
        verify_workbook(export, synthetic_workbook(export, formula=True))
    source, xlsx = tmp_path / "synthetic.json", tmp_path / "synthetic.xlsx"
    source.write_text(export.model_dump_json().replace('"cases":', '"cases":[],"cases":'))
    xlsx.write_bytes(workbook)
    with pytest.raises(ValueError, match="dupliqu"):
        load_freiburg(source, xlsx)


def test_no_write_modes_and_private_review(
    draft_store: FreiburgDraftStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = synthetic_freiburg()
    source, xlsx = tmp_path / "synthetic.json", tmp_path / "synthetic.xlsx"
    source.write_text(export.model_dump_json())
    xlsx.write_bytes(synthetic_workbook(export))
    initial = source.read_bytes(), xlsx.read_bytes()
    absent = tmp_path / "absent.db"
    for mode in ("--validate-only", "--dry-run"):
        assert (
            run(
                [
                    "--database-url",
                    f"sqlite:///{absent}",
                    "import-freiburg",
                    str(source),
                    "--workbook",
                    str(xlsx),
                    mode,
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["ecriture"] is False
        assert not absent.exists()
    args = [
        "--database-url",
        str(draft_store.engine.url),
        "import-freiburg",
        str(source),
        "--workbook",
        str(xlsx),
    ]
    assert run(args) == 0
    assert json.loads(capsys.readouterr().out)["cas_importes_cette_operation"] == 1
    assert run(args) == 0
    assert json.loads(capsys.readouterr().out)["cas_identiques"] == 1
    assert initial == (source.read_bytes(), xlsx.read_bytes())
    assert run(["--database-url", str(draft_store.engine.url), "inspect-freiburg"]) == 0
    review = capsys.readouterr().out
    assert "SYNTHETIC contradiction" in review and '"rights": "unknown"' in review
    assert draft_store.inspect()["cas_importes"] == 1


def test_intake_idempotency_rollback_immutability_and_no_publication(
    draft_store: FreiburgDraftStore,
) -> None:
    export = synthetic_freiburg()
    assert draft_store.import_export(export, {}, dry_run=True)["cas_nouveaux"] == 1
    assert draft_store.inspect()["cas_importes"] == 0
    draft_store.import_export(export, {})
    assert draft_store.import_export(export, {})["cas_identiques"] == 1
    raw = export.model_dump(mode="json")
    raw["cases"][0]["title_de"] = "CHANGED TEST"
    with pytest.raises(InvalidStateError, match="Conflit"):
        draft_store.import_export(FreiburgExport.model_validate_json(json.dumps(raw)), {})
    assert draft_store.inspect()["cas"][0]["material"]["case"]["title_de"] == "SYNTHETIC TEST"
    with pytest.raises(DBAPIError), draft_store.engine.begin() as db:
        db.execute(update(DraftCaseRow).values(status="published"))
    with pytest.raises(DBAPIError), draft_store.engine.begin() as db:
        db.execute(update(DraftBatchRow).values(payload={}))
    with pytest.raises(DBAPIError), draft_store.engine.begin() as db:
        db.execute(text("DELETE FROM clinical_draft_cases"))
    with pytest.raises(DBAPIError), draft_store.engine.begin() as db:
        db.execute(
            DraftCaseRow.__table__.insert().values(
                id="new",
                version="1",
                content_hash="0" * 64,
                batch_id="missing",
                status="draft_unvalidated",
            )
        )
    # Late database error rolls back the already flushed batch too.
    raw["cases"][0]["version"] = "next"
    with draft_store.engine.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER fail_draft BEFORE INSERT ON clinical_draft_cases "
                "BEGIN SELECT RAISE(ABORT, 'SYNTHETIC ERROR'); END"
            )
        )
    with pytest.raises(DBAPIError):
        draft_store.import_export(FreiburgExport.model_validate_json(json.dumps(raw)), {})
    with draft_store.engine.connect() as db:
        assert len(db.execute(select(DraftBatchRow.id)).all()) == 1
        assert db.scalar(text("SELECT count(*) FROM clinical_scenarios")) == 0
        assert db.scalar(text("SELECT count(*) FROM clinical_reviews")) == 0
    with pytest.raises(NotFoundError):
        ClinicalStore(draft_store.engine).publish("SYN-CASE", "0.1-draft", actor="NO APPROVAL")
