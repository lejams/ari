"""Learner accounts: address first, password from the e-mailed link, sessions, alpha adoption."""

from __future__ import annotations

import logging
import smtplib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from accounts_fixtures import PASSWORD, last_link, sent_to, sign_in
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.application.ports.email import EmailMessage
from ari.application.services.accounts import MAX_FAILED_LOGINS, LearnerAuth
from ari.container import Container
from ari.domain.errors import EmailDeliveryError, InvalidStateError
from ari.domain.models import CEFRLevel
from ari.infrastructure.persistence.platform.accounts import SqlLearnerAccountStore
from ari.infrastructure.persistence.platform.identity import ProfileCredentials
from ari.infrastructure.providers.email import SmtpEmailSender


def test_signup_link_password_then_onboarding_opens_the_app(container: Container) -> None:
    with TestClient(create_app(container), follow_redirects=False) as client:
        assert (
            client.post("/api/auth/signup", json={"email": " Ana@Example.TEST "}).status_code == 202
        )
        (text,) = sent_to(client, "ana@example.test")
        assert "http://localhost:8000/connexion#jeton=" in text
        token = last_link(client, "ana@example.test")
        link = client.post("/api/auth/links", json={"token": token}).json()
        assert link == {"email": "ana@example.test", "activation": True}
        # A refused password does not spend the link.
        short = client.post("/api/auth/password", json={"token": token, "password": "court"})
        assert short.status_code == 400
        created = client.post("/api/auth/password", json={"token": token, "password": PASSWORD})
        assert created.status_code == 200
        cookie = created.headers["set-cookie"]
        assert "ari_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert created.json() == {"email": "ana@example.test", "has_learner": False}
        assert client.get("/app").status_code == 200
        assert client.get("/voice.html").headers["location"] == "/app"  # Onboarding first.
        learner = client.post("/api/learners", json={"target_cefr": "B2"})
        assert learner.status_code == 201
        assert client.get("/api/auth/me").json()["has_learner"] is True
        assert client.get("/voice.html").status_code == 200
        again = client.post("/api/auth/password", json={"token": token, "password": PASSWORD})
        assert again.status_code == 400
        assert client.post("/api/auth/links", json={"token": token}).status_code == 404


def test_visitors_without_a_session_are_sent_to_sign_in(container: Container) -> None:
    with TestClient(create_app(container), follow_redirects=False) as client:
        for page in ("/app", "/voice.html"):
            response = client.get(page)
            assert response.status_code == 303
            assert response.headers["location"] == "/connexion"
        assert client.get("/connexion").status_code == 200
        assert client.get("/").status_code == 200
        assert client.post("/api/learners", json={}).status_code == 401
        assert client.get("/api/auth/me").status_code == 401


def test_answers_never_reveal_an_account_and_links_are_throttled(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        assert client.post("/api/auth/password/forgot", json={"email": "nobody@example.test"})
        assert sent_to(client, "nobody@example.test") == []
        for _ in range(3):
            response = client.post("/api/auth/signup", json={"email": "zoe@example.test"})
            assert response.status_code == 202
        assert len(sent_to(client, "zoe@example.test")) == 1  # One link per minute at most.
        body = {"token": last_link(client, "zoe@example.test"), "password": PASSWORD}
        assert client.post("/api/auth/password", json=body).status_code == 200
        # Signing up again with a registered address answers the same, and tells its owner.
        assert (
            client.post("/api/auth/signup", json={"email": "zoe@example.test"}).status_code == 202
        )
        assert "a déjà un compte" in sent_to(client, "zoe@example.test")[-1]
        # One plain mailbox only: nothing a mail header would read as several recipients.
        for invalid in (
            "pas-une-adresse",
            "victim@example.test,attacker@evil.test",
            "a@b.test;c@d.test",
            '"x@y.test"@evil.test',
            "a@b.test\r\nbcc:z@evil.test",
        ):
            response = client.post("/api/auth/signup", json={"email": invalid})
            assert response.status_code == 400, invalid


def test_a_failed_delivery_is_logged_and_answers_like_any_other(
    container: Container, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(_: EmailMessage) -> None:
        raise EmailDeliveryError("relay down")

    # Alembic's fileConfig (run in-process by the database fixture) disables loggers that
    # already exist; switch this one back on to observe it.
    monkeypatch.setattr(logging.getLogger("ari.api.auth"), "disabled", False)
    with TestClient(create_app(container)) as client:
        monkeypatch.setattr(container.email, "send", refuse)
        response = client.post("/api/auth/signup", json={"email": "down@example.test"})
        assert response.status_code == 202
        assert "account e-mail not delivered" in caplog.text


def test_setting_a_password_closes_every_other_open_link(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        email = sign_in(client)
        first = client.post("/api/auth/password/forgot", json={"email": email})
        assert first.status_code == 202
        stale = last_link(client, email)
        auth = container.auth
        # A second reset link for the same account, issued after the cooldown.
        store = SqlLearnerAccountStore(container.repository.engine)
        later = LearnerAuth(
            store,
            public_url="http://localhost:8000",
            clock=lambda: datetime.now(UTC) + timedelta(minutes=2),
        )
        message = later.request_password_reset(email)
        assert message is not None
        fresh = message.text.split("#jeton=")[1].split()[0]
        assert auth.link_holder(stale) is not None
        body = {"token": fresh, "password": "a second long passphrase"}
        assert client.post("/api/auth/password", json=body).status_code == 200
        assert auth.link_holder(stale) is None
        reuse = {"token": stale, "password": "an attacker's passphrase"}
        assert client.post("/api/auth/password", json=reuse).status_code == 400


def test_login_attempts_are_reserved_before_the_password_is_checked(container: Container) -> None:
    store = SqlLearnerAccountStore(container.repository.engine)
    with TestClient(create_app(container)) as client:
        email = sign_in(client)
    account = store.by_email(email)
    assert account is not None
    now = datetime.now(UTC)
    reserve = [
        store.reserve_login_attempt(
            account.id, now, max_failures=MAX_FAILED_LOGINS, lockout=timedelta(minutes=15)
        )
        for _ in range(MAX_FAILED_LOGINS + 3)
    ]
    # However many guesses run at once, only the first five reach the password check.
    assert reserve == [True] * MAX_FAILED_LOGINS + [False] * 3
    later = now + timedelta(minutes=16)
    assert store.reserve_login_attempt(
        account.id, later, max_failures=MAX_FAILED_LOGINS, lockout=timedelta(minutes=15)
    )


def test_an_address_without_a_chosen_password_cannot_log_in(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        client.post("/api/auth/signup", json={"email": "idle@example.test"})
        body = {"email": "idle@example.test", "password": PASSWORD}
        refused = client.post("/api/auth/login", json=body)
        assert refused.status_code == 400
        assert refused.json()["detail"] == "Identifiants invalides"


def test_lockout_then_reset_logs_out_everywhere_and_unlocks(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as first, TestClient(app) as second:
        email = sign_in(first)
        wrong = {"email": email, "password": "not the password"}
        for _ in range(MAX_FAILED_LOGINS):
            assert second.post("/api/auth/login", json=wrong).json()["detail"] == (
                "Identifiants invalides"
            )
        locked = second.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert "verrouillé" in locked.json()["detail"]
        assert second.post("/api/auth/password/forgot", json={"email": email}).status_code == 202
        link = second.post("/api/auth/links", json={"token": last_link(second, email)}).json()
        assert link["activation"] is False
        new = {"token": last_link(second, email), "password": "an entirely new passphrase"}
        assert second.post("/api/auth/password", json=new).status_code == 200
        assert first.get("/api/auth/me").status_code == 401  # The reset revoked it.
        assert second.get("/api/auth/me").json()["email"] == email
        again = {"email": email, "password": "an entirely new passphrase"}
        assert first.post("/api/auth/login", json=again).status_code == 200


def test_sessions_and_links_expire_on_the_server(container: Container) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    auth = LearnerAuth(
        SqlLearnerAccountStore(container.repository.engine),
        public_url="https://ari.example.test/",
        session_days=30,
        reset_minutes=60,
        clock=lambda: now,
    )
    activation = auth.signup("clock@example.test")
    assert activation is not None
    assert "https://ari.example.test/connexion#jeton=" in activation.text
    token = activation.text.split("#jeton=")[1].split()[0]
    session = auth.set_password(token, PASSWORD)
    assert auth.resolve(session) is not None
    now += timedelta(days=30)
    assert auth.resolve(session) is None
    reset_message = auth.request_password_reset("clock@example.test")
    assert reset_message is not None
    reset = reset_message.text.split("#jeton=")[1].split()[0]
    now += timedelta(minutes=61)
    with pytest.raises(InvalidStateError):
        auth.set_password(reset, PASSWORD)


def test_an_alpha_profile_joins_the_first_account_of_its_browser(container: Container) -> None:
    credentials = ProfileCredentials(container.repository.engine)
    alpha = container.orchestrator.create_learner(CEFRLevel.B2)
    legacy = credentials.issue(alpha.id)
    app = create_app(container)
    with TestClient(app) as browser, TestClient(app) as other:
        browser.cookies.set("ari_profile", legacy)
        email = browser.post("/api/auth/signup", json={"email": "alpha@example.test"})
        assert email.status_code == 202
        body = {"token": last_link(browser, "alpha@example.test"), "password": PASSWORD}
        signed = browser.post("/api/auth/password", json=body)
        assert signed.json()["has_learner"] is True
        assert 'ari_profile=""' in signed.headers["set-cookie"]  # The old cookie is cleared.
        assert browser.get("/api/profile").json()["id"] == alpha.id
        assert credentials.resolve(legacy) is None
        # The same stale cookie elsewhere adopts nothing: the credential is gone.
        other.cookies.set("ari_profile", legacy)
        sign_in(other)
        assert other.get("/api/auth/me").json()["has_learner"] is False


def test_a_forwarded_link_never_adopts_the_alpha_profile_of_its_opener(
    container: Container,
) -> None:
    credentials = ProfileCredentials(container.repository.engine)
    alpha = container.orchestrator.create_learner(CEFRLevel.B2)
    legacy = credentials.issue(alpha.id)
    app = create_app(container)
    with TestClient(app) as attacker, TestClient(app) as victim:
        attacker.post("/api/auth/signup", json={"email": "attacker@example.test"})
        forwarded = last_link(attacker, "attacker@example.test")
        victim.cookies.set("ari_profile", legacy)
        body = {"token": forwarded, "password": PASSWORD}
        opened = victim.post("/api/auth/password", json=body)
        assert opened.json() == {"email": "attacker@example.test", "has_learner": False}
        cookies = opened.headers.get_list("set-cookie")
        assert not any(cookie.startswith("ari_profile") for cookie in cookies)
        assert credentials.resolve(legacy) == alpha.id  # Still the victim's, still usable.
        assert victim.cookies.get("ari_profile") == legacy


class _RecordingSmtp:
    instances: list[_RecordingSmtp] = []  # noqa: RUF012
    fail = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls: list[tuple[str, Any]] = [("connect", (host, port, timeout))]
        _RecordingSmtp.instances.append(self)

    def __enter__(self) -> _RecordingSmtp:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self) -> None:
        self.calls.append(("starttls", None))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username))

    def send_message(self, message: Any, to_addrs: list[str] | None = None) -> None:
        if _RecordingSmtp.fail:
            raise smtplib.SMTPRecipientsRefused({})
        content = (to_addrs, message["To"], message["Subject"], message.get_content())
        self.calls.append(("send", content))


def test_smtp_sender_uses_starttls_and_reports_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSmtp)
    sender = SmtpEmailSender(
        host="smtp.example.test",
        port=587,
        sender="ARI <no-reply@example.test>",
        username="relay",
        password="secret",
        security="starttls",
        timeout_seconds=5.0,
    )
    sender.send(EmailMessage(to="a@example.test", subject="Sujet", text="Corps"))
    calls = _RecordingSmtp.instances[-1].calls
    assert [name for name, _ in calls] == ["connect", "starttls", "login", "send"]
    assert calls[-1][1] == (["a@example.test"], "a@example.test", "Sujet", "Corps\n")
    _RecordingSmtp.fail = True
    try:
        with pytest.raises(EmailDeliveryError):
            sender.send(EmailMessage(to="a@example.test", subject="Sujet", text="Corps"))
    finally:
        _RecordingSmtp.fail = False
