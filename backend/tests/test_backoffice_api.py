"""Back-office API: invitations, sessions, roles, upload, review to gold, exports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from content_fixtures import synthetic_protocol_pdf
from fastapi.testclient import TestClient
from test_protocol_workflow import settle

from ari.backoffice_api.app import create_backoffice_app
from ari.backoffice_api.container import Backoffice
from ari.content.domain.accounts import Role
from ari.content.domain.protocol import ProtocolRecord
from ari.worker import run_one

PASSWORD = "correct horse battery"


def invite(services: Backoffice, email: str, name: str, *roles: Role) -> str:
    return services.auth.create_account(email, name, set(roles), actor=None).token


def signed_in(app, services: Backoffice, email: str, name: str, *roles: Role) -> TestClient:  # type: ignore[no-untyped-def]
    token = invite(services, email, name, *roles)
    client = TestClient(app)
    response = client.post(f"/api/auth/invitations/{token}/password", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def drain(services: Backoffice) -> None:
    async def loop() -> None:
        while await run_one(services.content, "test-worker") is not None:
            pass

    asyncio.run(loop())


def test_invitation_sets_the_password_once_and_sessions_are_server_side(
    backoffice: Backoffice,
) -> None:
    app = create_backoffice_app(backoffice)
    token = invite(backoffice, "Owner@Example.org", "Owner", Role.OWNER)
    with TestClient(app) as client:
        assert client.get("/api/protocols").status_code == 401
        assert client.get(f"/api/auth/invitations/{token}").json() == {
            "email": "owner@example.org",
            "display_name": "Owner",
        }
        short = client.post(f"/api/auth/invitations/{token}/password", json={"password": "short"})
        assert short.status_code == 400 and "12 caractères" in short.json()["detail"]
        me = client.post(f"/api/auth/invitations/{token}/password", json={"password": PASSWORD})
        assert me.status_code == 200 and me.json()["roles"] == ["owner"]
        assert client.get("/api/auth/me").json()["display_name"] == "Owner"
        # The link is one-time.
        assert client.get(f"/api/auth/invitations/{token}").status_code == 404
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"email": "owner@example.org", "password": PASSWORD}
            ).status_code
            == 200
        )
        # A foreign origin cannot mutate even with the cookie.
        forged = client.post("/api/documents/x/release", headers={"Origin": "https://evil.example"})
        assert forged.status_code == 403


def test_login_locks_after_five_failures(backoffice: Backoffice) -> None:
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "d@example.org", "Doc", Role.PHYSICIAN_REVIEWER) as client:
        client.post("/api/auth/logout")
        for _ in range(5):
            bad = client.post(
                "/api/auth/login", json={"email": "d@example.org", "password": "x" * 12}
            )
            assert bad.status_code == 400 and bad.json()["detail"] == "Identifiants invalides"
        locked = client.post(
            "/api/auth/login", json={"email": "d@example.org", "password": PASSWORD}
        )
        assert locked.status_code == 400 and "verrouillé" in locked.json()["detail"]
    account = backoffice.accounts.by_email("d@example.org")
    assert account is not None and account.locked_until is not None
    assert account.locked_until > datetime.now(UTC) + timedelta(minutes=10)
    assert [e["action"] for e in backoffice.accounts.audit_entries(action="login_failed")] == [
        "login_failed"
    ] * 5


def test_roles_guard_administration_and_deactivation_revokes_sessions(
    backoffice: Backoffice,
) -> None:
    app = create_backoffice_app(backoffice)
    with (
        signed_in(app, backoffice, "o@example.org", "Owner", Role.OWNER) as owner,
        signed_in(app, backoffice, "d@example.org", "Doc", Role.PHYSICIAN_REVIEWER) as doctor,
    ):
        assert doctor.get("/api/accounts").status_code == 403
        assert doctor.post("/api/documents/x/release").status_code == 403
        created = owner.post(
            "/api/accounts",
            json={
                "email": "l@example.org",
                "display_name": "Ling",
                "roles": ["linguistic_reviewer"],
            },
        )
        assert created.status_code == 201
        assert created.json()["invitation_url"].startswith("http://backoffice.test/#/invitation/")
        listed = owner.get("/api/accounts").json()
        assert [a["email"] for a in listed["items"]] == [
            "o@example.org",
            "d@example.org",
            "l@example.org",
        ]
        doctor_id = next(a["id"] for a in listed["items"] if a["email"] == "d@example.org")
        assert owner.post(f"/api/accounts/{doctor_id}/deactivate").json()["active"] is False
        assert doctor.get("/api/auth/me").status_code == 401
        assert owner.get("/api/dashboard").json()["me"]["roles"] == ["owner"]


def test_upload_review_and_gold_through_the_api(backoffice: Backoffice, tmp_path: Path) -> None:
    app = create_backoffice_app(backoffice)
    pdf = synthetic_protocol_pdf(tmp_path / "protokolle.pdf", count=1)
    with (
        signed_in(app, backoffice, "o@example.org", "Owner", Role.OWNER) as owner,
        signed_in(app, backoffice, "d@example.org", "Doc", Role.PHYSICIAN_REVIEWER) as doctor,
    ):
        form = {
            "provenance": "Synthetic PDF from the test suite",
            "consent_declaration": "No real person involved",
            "rights": "compatible",
            "rights_evidence": "Generated fixture",
            "land": "Bayern",
            "exam_date": "2026-03",
        }
        assert (
            doctor.post(
                "/api/documents",
                data=form,
                files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")},
            ).status_code
            == 403
        )
        uploaded = owner.post(
            "/api/documents",
            data=form,
            files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["document"]["id"]
        again = owner.post(
            "/api/documents",
            data=form,
            files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")},
        )
        assert again.status_code == 200 and again.json()["duplicate"] is True
        assert (
            owner.post(
                "/api/documents", data=form, files={"file": ("x.pdf", b"nope", "application/pdf")}
            ).status_code
            == 400
        )

        drain(backoffice)
        detail = owner.get(f"/api/documents/{document_id}").json()
        assert detail["document"]["status"] == "extracted"
        assert (
            len(detail["segments"]) == 1
            and detail["segments"][0]["protocol"]["status"] == "extracted"
        )
        assert detail["jobs"]["succeeded"] == 3
        page = doctor.get(f"/api/documents/{document_id}/pages/2").json()
        assert "Protokoll 1" in page["text"]
        image = doctor.get(f"/api/documents/{document_id}/pages/2/image")
        assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
        original = doctor.get(f"/api/documents/{document_id}/original")
        assert original.headers["content-type"] == "application/pdf"
        assert "attachment" in original.headers["content-disposition"]

        assert owner.post(f"/api/documents/{document_id}/release").json() == {"released": 1}
        queue = doctor.get("/api/protocols", params={"status": "doctor_review"}).json()
        assert queue["total"] == 1 and queue["items"][0]["land"] == "Bayern"
        protocol_id = queue["items"][0]["id"]
        head = doctor.get(f"/api/protocols/{protocol_id}").json()
        assert head["blockers"]["doctor"] and head["diff_to_previous"] == []
        settled = settle(
            ProtocolRecord.model_validate_json(__import__("json").dumps(head["record"]))
        )
        stale = doctor.post(
            f"/api/protocols/{protocol_id}/versions",
            json={
                "base_version": 1,
                "base_hash": "0" * 64,
                "record": settled.model_dump(mode="json"),
            },
        )
        assert stale.status_code == 409
        broken = dict(settled.model_dump(mode="json"))
        broken["anamnesis"][0]["polarity"] = "unknown"  # value kept: contract violation
        assert (
            doctor.post(
                f"/api/protocols/{protocol_id}/versions",
                json={"base_version": 1, "base_hash": head["content_hash"], "record": broken},
            ).status_code
            == 422
        )
        revised = doctor.post(
            f"/api/protocols/{protocol_id}/versions",
            json={
                "base_version": 1,
                "base_hash": head["content_hash"],
                "record": settled.model_dump(mode="json"),
            },
        )
        assert revised.status_code == 201, revised.text
        body = revised.json()
        assert body["version"] == 2 and body["blockers"]["doctor"] == []
        assert any(c["path"] == "pedagogy.difficulty" for c in body["diff_to_previous"])
        decision = {"version": 2, "protocol_hash": body["content_hash"], "decision": "approve"}
        assert (
            owner.post(f"/api/protocols/{protocol_id}/decisions/doctor", json=decision).status_code
            == 403
        )
        approved = doctor.post(f"/api/protocols/{protocol_id}/decisions/doctor", json=decision)
        assert approved.status_code == 200 and approved.json()["status"] == "doctor_approved"
        gold = owner.post(
            f"/api/protocols/{protocol_id}/decisions/owner", json={**decision, "notes": "OK"}
        )
        assert gold.status_code == 200 and gold.json()["status"] == "gold"
        assert gold.json()["gold"]["location"]["land"] == "Bayern"
        listed = doctor.get("/api/gold").json()
        assert listed["total"] == 1 and listed["items"][0]["protocol_id"] == protocol_id
        export = owner.get("/api/gold/export")
        assert export.status_code == 200 and export.text.count("\n") == 1
        assert doctor.get("/api/gold/export").status_code == 403
        assert owner.delete(f"/api/protocols/{protocol_id}").status_code == 409
        dashboard = owner.get("/api/dashboard").json()
        assert dashboard["gold_by_land"] == {"Bayern": 1} and dashboard["protocols_by_status"] == {
            "gold": 1
        }
        assert owner.get("/api/meta/lands").json()[1] == "Bayern"


def test_owner_can_remove_an_unvalidated_protocol_without_erasing_audit(
    backoffice: Backoffice, tmp_path: Path
) -> None:
    app = create_backoffice_app(backoffice)
    pdf = synthetic_protocol_pdf(tmp_path / "protocole-a-supprimer.pdf", count=1)
    with (
        signed_in(app, backoffice, "o@example.org", "Owner", Role.OWNER) as owner,
        signed_in(app, backoffice, "d@example.org", "Doc", Role.PHYSICIAN_REVIEWER) as doctor,
    ):
        uploaded = owner.post(
            "/api/documents",
            data={
                "provenance": "Synthetic PDF from the test suite",
                "consent_declaration": "No real person involved",
                "rights": "compatible",
                "rights_evidence": "Generated fixture",
                "land": "Bayern",
            },
            files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["document"]["id"]
        drain(backoffice)
        protocol_id = owner.get(f"/api/documents/{document_id}").json()["segments"][0][
            "protocol"
        ]["id"]

        assert doctor.delete(f"/api/protocols/{protocol_id}").status_code == 403
        removed = owner.delete(f"/api/protocols/{protocol_id}")
        assert removed.status_code == 204

        detail = owner.get(f"/api/protocols/{protocol_id}").json()
        assert detail["status"] == "rejected"
        assert detail["events"][-1]["event_type"] == "deleted"
