"""PDF → gold → bundle draft → registry → two reviews → publish → visible to learners.

Runs on the two databases with the fake model: the whole path a real protocol takes, driven
through the back-office API by three accounts (owner, physician, linguist).
"""

from __future__ import annotations

import json
from pathlib import Path

from accounts_fixtures import sign_in
from conftest import build_test_container
from content_fixtures import synthetic_protocol_pdf
from fastapi.testclient import TestClient
from test_backoffice_api import drain, signed_in
from test_protocol_workflow import settle

from ari.api.app import create_app
from ari.backoffice_api.app import create_backoffice_app
from ari.backoffice_api.container import Backoffice
from ari.content.domain.accounts import Role
from ari.content.domain.protocol import ProtocolRecord
from ari.domain.clinical import CaseReview

FORM = {
    "provenance": "Synthetic PDF from the test suite",
    "consent_declaration": "No real person involved",
    "rights": "compatible",
    "rights_evidence": "Generated fixture",
    "land": "Bayern",
    "exam_date": "2026-03",
}


def test_gold_protocol_becomes_a_published_case_learners_can_see(
    backoffice: Backoffice, tmp_path: Path
) -> None:
    app = create_backoffice_app(backoffice)
    pdf = synthetic_protocol_pdf(tmp_path / "protokolle.pdf", count=1)
    with (
        signed_in(app, backoffice, "o@example.org", "Owner", Role.OWNER) as owner,
        signed_in(app, backoffice, "d@example.org", "Doc", Role.PHYSICIAN_REVIEWER) as doctor,
        signed_in(app, backoffice, "l@example.org", "Ling", Role.LINGUISTIC_REVIEWER) as linguist,
    ):
        # --- protocol to gold (covered in detail by test_backoffice_api) ---
        uploaded = owner.post(
            "/api/documents",
            data=FORM,
            files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")},
        )
        document_id = uploaded.json()["document"]["id"]
        drain(backoffice)
        owner.post(f"/api/documents/{document_id}/release")
        protocol_id = doctor.get("/api/protocols").json()["items"][0]["id"]
        head = doctor.get(f"/api/protocols/{protocol_id}").json()
        settled = settle(ProtocolRecord.model_validate_json(json.dumps(head["record"])))
        revised = doctor.post(
            f"/api/protocols/{protocol_id}/versions",
            json={
                "base_version": 1,
                "base_hash": head["content_hash"],
                "record": settled.model_dump(mode="json"),
            },
        ).json()
        decision = {"version": 2, "protocol_hash": revised["content_hash"], "decision": "approve"}
        doctor.post(f"/api/protocols/{protocol_id}/decisions/doctor", json=decision)
        gold = owner.post(f"/api/protocols/{protocol_id}/decisions/owner", json=decision).json()
        assert gold["status"] == "gold"

        # --- bundle draft: owner requests, worker generates, everyone can read ---
        before = doctor.get(f"/api/gold/{protocol_id}/bundle-drafts").json()
        assert before["available_phases"] == ["arzt_patient", "arzt_arzt", "fachbegriffe"]
        assert before["items"] == [] and before["next_revision"] == 1
        request = {"phases": ["arzt_patient", "arzt_arzt", "fachbegriffe"], "cefr": "B2"}
        assert (
            doctor.post(f"/api/gold/{protocol_id}/bundle-drafts", json=request).status_code == 403
        )
        queued = owner.post(f"/api/gold/{protocol_id}/bundle-drafts", json=request)
        assert queued.status_code == 202 and queued.json()["status"] == "queued"
        pending = owner.get(f"/api/gold/{protocol_id}/bundle-drafts").json()
        assert [job["id"] for job in pending["jobs"]] == [queued.json()["id"]]
        drain(backoffice)
        drafts = owner.get(f"/api/gold/{protocol_id}/bundle-drafts").json()
        assert drafts["jobs"] == [] and drafts["total"] == 1 and drafts["next_revision"] == 2
        draft = drafts["items"][0]
        assert draft["status"] == "draft" and draft["case_id"] == f"FSP-BY-{protocol_id}"
        detail = linguist.get(f"/api/bundle-drafts/{draft['id']}").json()
        assert detail["bundle"]["cases"][0]["location"]["land"] == "Bayern"

        # --- import into the registry: owner only, idempotent ---
        assert doctor.post(f"/api/bundle-drafts/{draft['id']}/import").status_code == 403
        imported = owner.post(f"/api/bundle-drafts/{draft['id']}/import")
        assert imported.status_code == 200, imported.text
        assert imported.json()["status"] == "imported" and imported.json()["imported_at"]
        again = owner.post(f"/api/bundle-drafts/{draft['id']}/import")
        assert again.status_code == 200 and again.json()["status"] == "imported"
        scenarios = doctor.get("/api/registry/scenarios").json()
        assert scenarios["total"] == 3
        assert {s["status"] for s in scenarios["items"]} == {"draft_unvalidated"}
        assert {s["land"] for s in scenarios["items"]} == {"Bayern"}
        voice = next(s for s in scenarios["items"] if s["phase"] == "arzt_patient")
        path = f"/api/registry/scenarios/{voice['id']}/{voice['version']}"
        inspected = owner.get(path).json()
        assert inspected["status"] == "draft_unvalidated"
        assert "Approbation humaine clinical manquante" in " ".join(inspected["blockers"])
        assert inspected["markdown"].startswith("# Revue locale ARI")
        assert owner.post(f"{path}/publish").status_code == 400  # blockers

        # --- two human reviews by two accounts holding the matching roles ---
        clinical = {"review_type": "clinical", "decision": "approve", "notes": "Cliniquement OK"}
        linguistic = {"review_type": "linguistic", "decision": "approve", "notes": "Langue OK"}
        assert owner.post(f"{path}/reviews", json=clinical).status_code == 403
        assert doctor.post(f"{path}/reviews", json=linguistic).status_code == 403
        recorded = doctor.post(f"{path}/reviews", json=clinical)
        assert recorded.status_code == 201, recorded.text
        review = CaseReview.model_validate_json(json.dumps(recorded.json()))
        assert review.reviewer_name == "Doc" and review.reviewer_account_id is not None
        assert linguist.post(f"{path}/reviews", json=linguistic).status_code == 201
        published = owner.post(f"{path}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"
        assert doctor.post(f"{path}/publish").status_code == 403
        assert [
            s["status"]
            for s in doctor.get("/api/registry/scenarios", params={"status": "published"}).json()[
                "items"
            ]
        ] == ["published"]

    # --- the learner platform sees the case, located in its Land ---
    learner = build_test_container(backoffice.settings.database_url)
    try:
        listed = learner.cases.list()
        assert [(case.id, case.land.value if case.land else None) for case in listed] == [
            (f"FSP-BY-{protocol_id}", "Bayern")
        ]
        with TestClient(create_app(learner)) as client:
            cases = client.get("/api/cases").json()
            assert len(cases) == 1 and cases[0]["land"] == "Bayern"
            sign_in(client)
            client.post("/api/learners", json={"target_cefr": "C1"})
            summary = client.get("/api/cases/summary").json()
            assert summary["laender"] == [
                {"land": "Bayern", "cases": 1, "worked": 0, "share_worked": 0.0}
            ]
    finally:
        learner.repository.engine.dispose()


def test_one_account_cannot_give_both_approvals_and_old_reviews_keep_their_hash(
    backoffice: Backoffice,
) -> None:
    from clinical_fixtures import simulated_review, synthetic_bundle

    from ari.content.domain.accounts import AccountContext
    from ari.domain.errors import InvalidStateError

    legacy = simulated_review(synthetic_bundle(), "clinical")
    assert "reviewer_account_id" not in legacy.model_dump(mode="json")
    explicit_none = legacy.model_copy(update={"reviewer_account_id": None})
    assert legacy.content_hash == explicit_none.content_hash

    bundle = synthetic_bundle()
    backoffice.registry._store.import_bundle(bundle)
    scenario = bundle.scenarios[0]
    both = AccountContext(
        id="both-1",
        display_name="Both",
        roles=frozenset({Role.PHYSICIAN_REVIEWER, Role.LINGUISTIC_REVIEWER}),
    )
    backoffice.registry.record_review(
        scenario.id,
        scenario.version,
        review_type="clinical",
        decision="approve",
        notes="ok",
        account=both,
    )
    try:
        backoffice.registry.record_review(
            scenario.id,
            scenario.version,
            review_type="linguistic",
            decision="approve",
            notes="ok",
            account=both,
        )
    except InvalidStateError as exc:
        assert "deux approbations" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the second approval by the same account was accepted")
    # A request for changes by the same account is fine: only the double approval is refused.
    backoffice.registry.record_review(
        scenario.id,
        scenario.version,
        review_type="linguistic",
        decision="request_changes",
        notes="À revoir",
        account=both,
    )
