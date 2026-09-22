"""The compose environment lists must stay in step with Settings, and the learner app must
never receive the content-database credentials. `extra="ignore"` would silently swallow a
mistyped key, so we assert it here instead."""

from pathlib import Path

import yaml

from ari.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = PROJECT_ROOT / "docker-compose.yml"

# ARI_* keys consumed by the postgres image or by Caddy, not by Settings.
COMPOSE_ONLY = {
    "ARI_PLATFORM_DB_PASSWORD",
    "ARI_CONTENT_DB_PASSWORD",
    "ARI_DOMAIN",
    "ARI_BACKOFFICE_DOMAIN",
    "ARI_APP_BASIC_AUTH_USER",
    "ARI_APP_BASIC_AUTH_HASH",
}


def _services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_every_ari_env_key_is_a_settings_field_or_documented_compose_key() -> None:
    fields = set(Settings.model_fields)
    for name, service in _services().items():
        for key in service.get("environment", {}):
            if not key.startswith("ARI_"):
                continue
            if key in COMPOSE_ONLY:
                continue
            field = key[len("ARI_") :].lower()
            assert field in fields, f"{name}: {key} is not a Settings field"


def test_learner_app_never_receives_content_credentials() -> None:
    app = _services()["app"]
    assert "env_file" not in app, "app must not load .env wholesale"
    env = app.get("environment", {})
    assert not any(key.startswith("ARI_CONTENT_") for key in env), "app leaked content credentials"
    assert "POSTGRES_PASSWORD" not in env
