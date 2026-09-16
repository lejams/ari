import json
from pathlib import Path

import pytest
from clinical_fixtures import simulated_review, synthetic_bundle
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ari.config import PROJECT_ROOT
from ari.container import Container
from ari.domain.clinical import VersionRef
from ari.domain.errors import InvalidStateError
from ari.domain.models import CEFRLevel
from ari.infrastructure.cases.cli import run
from ari.infrastructure.cases.yaml_io import parse_bundle
from ari.infrastructure.persistence.clinical_rows import ClinicalCaseRow


def bundle_file(tmp_path: Path) -> Path:
    file = tmp_path / "synthetic.yaml"
    file.write_text(synthetic_bundle().model_dump_json(), encoding="utf-8")
    return file


def test_direct_ari_import_rejects_contact_data() -> None:
    raw = synthetic_bundle().model_dump(mode="json")
    raw["cases"][0]["title"] = "Telegram: @private_user"
    with pytest.raises(ValidationError, match="Coordonnées"):
        parse_bundle(json.dumps(raw))


def test_validate_only_and_dry_run_never_create_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    file, database = bundle_file(tmp_path), tmp_path / "must-not-exist.db"
    for mode in ("--validate-only", "--dry-run"):
        assert run(["--database-url", f"sqlite:///{database}", "import", str(file), mode]) == 0
        assert json.loads(capsys.readouterr().out)["ecriture"] is False
        assert not database.exists()


def test_cli_import_idempotence_draft_and_private_inspection(
    container: Container,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    file = bundle_file(tmp_path)
    prefix = ["--database-url", container.settings.database_url]
    assert run([*prefix, "import", str(file), "--dry-run"]) == 0
    with Session(container.repository.engine) as db:
        assert db.scalar(select(ClinicalCaseRow)) is None
    capsys.readouterr()
    assert run([*prefix, "import", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["cas_nouveaux"] == 1
    assert run([*prefix, "import", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["cas_identiques"] == 1
    assert run([*prefix, "inspect", "synthetic-scenario", "1", "--format", "markdown"]) == 0
    report = capsys.readouterr().out
    assert "draft_unvalidated" in report
    assert "Depuis 1 jours" in report
    assert "publication" in report
    assert run([*prefix, "publish", "synthetic-scenario", "1", "--actor", "TEST"]) == 2


def test_cli_offline_end_to_end_reviews_publish_and_diff(
    container: Container,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # All simulated approvals are authored solely inside this isolated test.
    bundle = synthetic_bundle()
    prefix = ["--database-url", container.settings.database_url]
    file = tmp_path / "synthetic.yaml"
    file.write_text(bundle.model_dump_json(), encoding="utf-8")
    assert run([*prefix, "import", str(file)]) == 0
    for kind in ("clinical", "linguistic"):
        review_file = tmp_path / f"{kind}.json"
        review_file.write_text(simulated_review(bundle, kind).model_dump_json(), encoding="utf-8")
        assert run([*prefix, "review", str(review_file)]) == 0
    assert run([*prefix, "eligibility", "synthetic-scenario", "1"]) == 0
    capsys.readouterr()
    assert run([*prefix, "publish", "synthetic-scenario", "1", "--actor", "TEST ONLY"]) == 0
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    initial = container.orchestrator.create_session(learner.id, "SYNTHETIC-TEST", "1")
    successor = synthetic_bundle("2")
    file.write_text(successor.model_dump_json(), encoding="utf-8")
    assert run([*prefix, "import", str(file)]) == 0
    capsys.readouterr()
    assert run([*prefix, "diff", "synthetic-scenario", "1", "2"]) == 0
    assert '"version": "2"' in capsys.readouterr().out
    assert (
        container.orchestrator.activate(initial.id).training_snapshot == initial.training_snapshot
    )


def test_unknown_rights_and_critical_questions_block_even_approved_case(
    container: Container,
) -> None:
    bundle = synthetic_bundle()
    source = bundle.sources[0].model_copy(update={"rights": "unknown", "rights_evidence": None})
    bundle = bundle.model_copy(update={"sources": (source,)})
    store = container.cases.store
    store.import_bundle(bundle)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    with pytest.raises(InvalidStateError, match="Droits"):
        store.publish("synthetic-scenario", "1", actor="TEST")


@pytest.mark.parametrize("phase", ["arzt_arzt", "fachbegriffe", "arztbrief"])
def test_future_phases_explicitly_unavailable(container: Container, phase: str) -> None:
    bundle = synthetic_bundle()
    scenario = bundle.scenarios[0].model_copy(update={"phase": phase})
    bundle = bundle.model_copy(update={"scenarios": (scenario,)})
    store = container.cases.store
    store.import_bundle(bundle)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    with pytest.raises(InvalidStateError, match="Phase"):
        store.publish("synthetic-scenario", "1", actor="TEST")
    assert not any(c.id == "SYNTHETIC-TEST" for c in container.cases.list())


def test_versioned_yaml_example_is_the_synthetic_contract() -> None:
    example = parse_bundle((PROJECT_ROOT / "cases/examples/synthetic_bundle.v2.yaml").read_text())
    assert example.content_hash == synthetic_bundle().content_hash


@pytest.mark.asyncio
async def test_v2_evaluation_delivery_and_pinned_terminology(container: Container) -> None:
    bundle = synthetic_bundle()
    scenario = bundle.scenarios[0].model_copy(
        update={
            "opening": "Seit 1 Tagen.",
            "opening_fact_ids": ("fact-1",),
        }
    )
    bundle = bundle.model_copy(update={"scenarios": (scenario,)})
    store = container.cases.store
    store.import_bundle(bundle)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    store.publish(scenario.id, scenario.version, actor="ISOLATED TEST")
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    session = container.orchestrator.create_session(learner.id, "SYNTHETIC-TEST", "1")
    container.orchestrator.activate(session.id)
    outcome = await container.orchestrator.process_transcript(session.id, "Guten Tag. Darf ich?")
    assert outcome.turn.revealed_fact_ids == ()
    repo = container.repository
    repo.begin_audio_stream(session.id, outcome.turn.id, "test-stream")
    repo.mark_audio_sent(session.id, outcome.turn.id, "test-stream", 1)
    repo.confirm_audio_started(
        session.id, outcome.turn.id, "test-stream", provider_response_id=None, last_index=0
    )
    repo.confirm_audio_delivered(
        session.id, outcome.turn.id, "test-stream", provider_response_id=None, last_index=0
    )
    analyzed = (await container.orchestrator.end_session(session.id)).session
    assert analyzed.evaluation is not None
    assert analyzed.evaluation.schema_version == "session-evaluation-v4"
    assert analyzed.evaluation.criteria[0]["scoring_version"] == "assessment-weighted-v1"
    clinical = next(
        c for c in analyzed.evaluation.criteria if c["criterion_id"] == "clinical_coverage"
    )
    assert clinical["score"] == 1.25
    terminology = bundle.terminology_sets[0].model_copy(update={"version": "2", "entries": ()})
    successor = scenario.model_copy(
        update={
            "version": "2",
            "terminology": VersionRef(id=terminology.id, version="2"),
            "terminology_hash": terminology.content_hash,
        }
    )
    updated = bundle.model_copy(
        update={"terminology_sets": (terminology,), "scenarios": (successor,)}
    )
    store.import_bundle(updated)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(updated, kind))
    store.publish(successor.id, successor.version, actor="ISOLATED TEST")
    resumed = (await container.orchestrator.end_session(session.id)).session
    assert resumed.evaluation == analyzed.evaluation
