"""Create activated learner accounts for the browser acceptance test."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ari.application.services.accounts import LearnerAuth
from ari.config import Settings
from ari.infrastructure.persistence.platform import create_platform_engine
from ari.infrastructure.persistence.platform.accounts import SqlLearnerAccountStore

PASSWORD = "correct horse battery"
TOKEN = re.compile(r"#jeton=([A-Za-z0-9_-]{1,128})")


def main(output: Path) -> None:
    settings = Settings(environment="development", provider_mode="fake")
    store = SqlLearnerAccountStore(create_platform_engine(settings.database_url))
    auth = LearnerAuth(store, public_url="http://127.0.0.1:8010")
    accounts = []
    for email in ("e2e-first@example.test", "e2e-second@example.test"):
        message = auth.signup(email)
        if message is None:
            raise RuntimeError(f"Could not create test account {email}")
        match = TOKEN.search(message.text)
        if match is None:
            raise RuntimeError(f"Activation link missing for {email}")
        auth.set_password(match.group(1), PASSWORD)
        accounts.append({"email": email, "password": PASSWORD})
    output.write_text(json.dumps(accounts), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
