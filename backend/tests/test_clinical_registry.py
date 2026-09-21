import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import yaml
from alembic import command
from clinical_fixtures import simulated_review, synthetic_bundle
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.application.services.assessment import weighted_assessment
from ari.container import Container
from ari.domain.clinical import ClinicalBundle, ClinicalFact
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import AudioDeliveryStatus, CEFRLevel, ConversationTurn
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.yaml_io import parse_bundle
from ari.infrastructure.persistence.platform.clinical_rows import (
    ClinicalCaseRow,
    PublicationEventRow,
)
from ari.infrastructure.persistence.platform.repository import SqlSessionRepository
from ari.infrastructure.persistence.platform.schema import alembic_config


def approve(store: ClinicalStore, bundle: ClinicalBundle) -> None:
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    store.publish(bundle.scenarios[0].id, bundle.scenarios[0].version, actor="TEST ONLY")


def test_canonical_hash_and_strict_schema() -> None:
    bundle = synthetic_bundle()
    raw = bundle.model_dump(mode="json")
    compact = parse_bundle(json.dumps(raw))
    formatted = parse_bundle(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    assert compact.content_hash == formatted.content_hash
    assert (
        compact.cases[0].content_hash
        != compact.cases[0].model_copy(update={"title": "Changed"}).content_hash
    )
    with pytest.raises(ValueError, match="dupliquée"):
        parse_bundle("schema_version: one\nschema_version: two")
    with pytest.raises(ValueError):
        parse_bundle("a: &a [1]\nb: *a")
    raw["published"] = True
    with pytest.raises(ValidationError):
        parse_bundle(json.dumps(raw))


@pytest.mark.parametrize(
    "change",
    [
        {"value": None},
        {"polarity": "unknown"},
        {"unit": "bananas"},
        {"value": "5", "unit": "mg"},
        {"value": True, "unit": "mg"},
        {"value": float("inf")},
    ],
)
def test_invalid_fact_values(change: dict[str, object]) -> None:
    raw = synthetic_bundle().cases[0].facts[0].model_dump(mode="json") | change
    with pytest.raises(ValidationError):
        ClinicalFact.model_validate_json(json.dumps(raw))


def test_absent_is_not_unknown() -> None:
    raw = synthetic_bundle().cases[0].facts[0].model_dump(mode="json")
    unknown = ClinicalFact.model_validate_json(
        json.dumps(raw | {"value": None, "unit": None, "polarity": "unknown"})
    )
    absent = ClinicalFact.model_validate_json(
        json.dumps(raw | {"value": "No test symptom", "unit": None, "polarity": "absent"})
    )
    assert unknown.content_hash != absent.content_hash


@pytest.mark.parametrize("section", ["facts", "assessment_items"])
def test_duplicate_ids(section: str) -> None:
    raw = synthetic_bundle().model_dump(mode="json")
    raw["cases"][0][section].append(raw["cases"][0][section][0])
    with pytest.raises(ValidationError, match="dupliqués"):
        parse_bundle(json.dumps(raw))


def test_unknown_refs_and_hash_mismatch() -> None:
    raw = synthetic_bundle().model_dump(mode="json")
    raw["cases"][0]["assessment_items"][0]["satisfied_by_fact_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="Fait inconnu"):
        parse_bundle(json.dumps(raw))
    raw = synthetic_bundle().model_dump(mode="json")
    raw["scenarios"][0]["rubric_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="Hash"):
        parse_bundle(json.dumps(raw))


def test_dry_run_idempotence_and_atomic_conflict(container: Container) -> None:
    store, bundle = container.cases.store, synthetic_bundle()
    assert store.import_bundle(bundle, dry_run=True)["cas_nouveaux"] == 1
    with Session(store.engine) as db:
        assert db.scalar(select(ClinicalCaseRow)) is None
    assert store.import_bundle(bundle)["cas_nouveaux"] == 1
    assert store.import_bundle(bundle)["cas_identiques"] == 1
    raw = synthetic_bundle("2").model_dump(mode="json")
    raw["scenarios"][0]["id"] = "synthetic-scenario"
    raw["scenarios"][0]["version"] = "1"  # Late scenario conflict, after case insert.
    with pytest.raises(InvalidStateError, match="Conflit"):
        store.import_bundle(parse_bundle(json.dumps(raw)))
    with Session(store.engine) as db:
        assert db.get(ClinicalCaseRow, ("SYNTHETIC-TEST", "2")) is None


def test_human_approvals_exact_content_and_latest_decision(container: Container) -> None:
    store, bundle = container.cases.store, synthetic_bundle()
    store.import_bundle(bundle)
    scenario = bundle.scenarios[0]
    with pytest.raises(InvalidStateError, match="Approbation"):
        store.publish(scenario.id, scenario.version, actor="TEST")
    store.record_review(simulated_review(bundle, "clinical"))
    with pytest.raises(InvalidStateError, match="linguistic"):
        store.publish(scenario.id, scenario.version, actor="TEST")
    wrong = simulated_review(bundle, "linguistic").model_copy(update={"case_hash": "0" * 64})
    with pytest.raises(InvalidStateError, match="exact"):
        store.record_review(wrong)
    store.record_review(simulated_review(bundle, "linguistic"))
    store.record_review(
        simulated_review(bundle, "clinical").model_copy(update={"decision": "request_changes"})
    )
    with pytest.raises(InvalidStateError, match="clinical"):
        store.publish(scenario.id, scenario.version, actor="TEST")


def test_publication_concurrency_and_historical_session(container: Container) -> None:
    store, bundle = container.cases.store, synthetic_bundle()
    store.import_bundle(bundle)
    with pytest.raises(NotFoundError):
        container.cases.get("SYNTHETIC-TEST", "1")
    assert all(c.id != "SYNTHETIC-TEST" for c in container.cases.list())
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))

    def publish_once() -> bool:
        try:
            store.publish("synthetic-scenario", "1", actor="TEST")
            return True
        except InvalidStateError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: publish_once(), range(2))) == [False, True]
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    session = container.orchestrator.create_session(learner.id, "SYNTHETIC-TEST", "1")
    case = container.cases.get(session.case_id, session.case_version)
    next_bundle = synthetic_bundle("2")
    store.import_bundle(next_bundle)
    assert len(store.inspect("synthetic-scenario", "2")["blockers"]) == 2
    approve(store, next_bundle)
    store.withdraw("synthetic-scenario", "1", actor="TEST")
    with pytest.raises(InvalidStateError, match="withdrawn"):
        container.orchestrator.create_session(learner.id, "SYNTHETIC-TEST", "1")
    restored = container.orchestrator.activate(session.id)
    assert restored.training_snapshot == session.training_snapshot
    assert restored.case_hash == case.content_hash
    assert (
        container.cases.get(session.case_id, session.case_version).content_hash == case.content_hash
    )
    with Session(store.engine) as db:
        assert len(db.scalars(select(PublicationEventRow)).all()) == 3


def test_weighted_delivery_and_behavior_evidence(container: Container) -> None:
    bundle = synthetic_bundle()
    container.cases.store.import_bundle(bundle)
    approve(container.cases.store, bundle)
    case = container.cases.get("SYNTHETIC-TEST", "1")
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    turn = ConversationTurn(
        id="test",
        session_id=session.id,
        sequence=1,
        user_text="Darf ich fragen?",
        patient_text="Seit 1 Tag.",
        revealed_fact_ids=("fact-1",),
        delivery_status=AudioDeliveryStatus.UNCONFIRMED,
    )
    scores = weighted_assessment(replace(session, turns=(turn,)), case)
    assert scores[0]["score"] == 0
    assert scores[1]["score"] == 5
    delivered = replace(turn, delivery_status=AudioDeliveryStatus.DELIVERED)
    scores = weighted_assessment(replace(session, turns=(delivered, delivered)), case)
    assert scores[0]["score"] == 1.25  # any weight 1; all weight 3 still missing.
    both = replace(delivered, revealed_fact_ids=("fact-1", "fact-2"), user_text="Frage")
    scores = weighted_assessment(replace(session, turns=(both, both)), case)
    assert scores[0]["score"] == 5
    assert scores[1]["score"] == 0  # Hearing a fact never proves a behavior.
    assert weighted_assessment(session, replace(case, assessment_items=()))[0]["score"] == 0


def test_migrated_registry_immutability_and_foreign_keys(database_url: str) -> None:
    command.check(alembic_config(database_url))
    repo = SqlSessionRepository(database_url)
    store = ClinicalStore(repo.engine)
    store.import_bundle(synthetic_bundle())
    with pytest.raises(IntegrityError), repo.engine.begin() as db:
        db.execute(update(ClinicalCaseRow).values(payload={"tampered": True}))
    with pytest.raises(IntegrityError), repo.engine.begin() as db:
        db.execute(text("INSERT INTO clinical_case_sources VALUES ('absent', '1', 'absent')"))
    with pytest.raises(IntegrityError), repo.engine.begin() as db:
        db.execute(text("DELETE FROM clinical_cases"))


def test_scenario_can_evolve_independently_and_session_uses_initial_pin(
    container: Container,
) -> None:
    bundle = synthetic_bundle()
    store = container.cases.store
    store.import_bundle(bundle)
    approve(store, bundle)
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    initial = container.orchestrator.create_session(learner.id, "SYNTHETIC-TEST", "1")
    original_case = container.orchestrator._case_for_session(initial)
    successor = bundle.scenarios[0].model_copy(update={"version": "2", "opening": "Hallo."})
    updated = bundle.model_copy(update={"scenarios": (successor,)})
    store.import_bundle(updated)
    approve(store, updated)
    newest = container.orchestrator.create_session(learner.id, "SYNTHETIC-TEST", "1")
    assert newest.case_hash == initial.case_hash
    assert newest.training_snapshot["scenario_version"] == "2"
    resumed = container.orchestrator.activate(initial.id)
    assert container.orchestrator._case_for_session(resumed).opening_statement == (
        original_case.opening_statement
    )
    assert container.cases.get("SYNTHETIC-TEST", "1").opening_statement == "Hallo."


def test_unknown_fact_keeps_authored_phrase_and_not_null_string(container: Container) -> None:
    bundle = synthetic_bundle()
    fact = (
        bundle.cases[0]
        .facts[0]
        .model_copy(
            update={
                "value": None,
                "unit": None,
                "polarity": "unknown",
                "patient_phrases_de": ("Das weiß ich nicht.",),
            }
        )
    )
    case = bundle.cases[0].model_copy(update={"facts": (fact, bundle.cases[0].facts[1])})
    scenario = bundle.scenarios[0].model_copy(update={"case_hash": case.content_hash})
    bundle = bundle.model_copy(update={"cases": (case,), "scenarios": (scenario,)})
    container.cases.store.import_bundle(bundle)
    approve(container.cases.store, bundle)
    runtime_fact = container.cases.get(case.id, case.version).facts[0]
    assert runtime_fact.polarity == "unknown"
    assert runtime_fact.value == "Das weiß ich nicht."


def test_mismatched_resource_hash_refuses_publication(container: Container) -> None:
    bundle = synthetic_bundle()
    store = container.cases.store
    store.import_bundle(bundle)
    from ari.infrastructure.persistence.platform.clinical_rows import SourceRow

    with store.engine.begin() as db:
        # Simulate storage corruption behind the immutability trigger's back.
        db.execute(text("DROP TRIGGER clinical_sources_no_update ON clinical_sources"))
        db.execute(update(SourceRow).values(content_hash="0" * 64))
    with pytest.raises(InvalidStateError, match="Intégrité"):
        store.inspect("synthetic-scenario", "1")
