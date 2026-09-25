"""Sign a test client in the way a learner does: address, e-mailed link, password."""

from __future__ import annotations

import re
from itertools import count

from fastapi.testclient import TestClient

from ari.infrastructure.providers.email import LogEmailSender

PASSWORD = "correct horse battery"
_LINK = re.compile(r"#jeton=([A-Za-z0-9_-]+)")
_serial = count(1)


def sent_to(client: TestClient, email: str) -> list[str]:
    """The e-mails the application under test wrote to its log for this address."""
    sender = client.app.state.container.email  # type: ignore[attr-defined]
    assert isinstance(sender, LogEmailSender)
    return [message.text for message in sender.sent if message.to == email]


def last_link(client: TestClient, email: str) -> str:
    texts = sent_to(client, email)
    assert texts, f"no e-mail to {email}"
    match = _LINK.search(texts[-1])
    assert match, texts[-1]
    return match.group(1)


def sign_in(client: TestClient, email: str | None = None) -> str:
    """A fresh, activated account whose session cookie now sits on this client."""
    address = email or f"learner-{next(_serial)}@example.test"
    assert client.post("/api/auth/signup", json={"email": address}).status_code == 202
    body = {"token": last_link(client, address), "password": PASSWORD}
    response = client.post("/api/auth/password", json=body)
    assert response.status_code == 200, response.text
    return address
