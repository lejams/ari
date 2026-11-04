"""Web pages: the public landing owns "/", sign-in "/connexion", the learner app "/app"."""

from __future__ import annotations

import re
from pathlib import Path

from accounts_fixtures import sign_in
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.container import Container

LANDING = Path(__file__).resolve().parents[2] / "web" / "landing.html"


def test_root_serves_the_landing_and_app_serves_the_learner_home(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert landing.headers["content-type"].startswith("text/html")
        assert "<title>ARI Allemand médical</title>" in landing.text
        assert client.head("/").status_code == 200
        assert "<title>Connexion · ARI</title>" in client.get("/connexion").text

        sign_in(client)
        learner = client.get("/app")
        assert learner.status_code == 200
        assert 'id="onboarding"' in learner.text

        # The app's assets keep their absolute paths under the static mount.
        assert client.get("/practice-ui.mjs").status_code == 200
        client.post("/api/learners", json={})
        assert "Consultation vocale" in client.get("/voice.html").text

        # Every page and module is revalidated, so a deploy is never hidden by a stale copy.
        for path in ("/", "/connexion", "/app", "/voice.html", "/practice-ui.mjs", "/app.js"):
            assert client.get(path).headers["cache-control"] == "no-cache", path


PUBLIC_LANDING_ASSETS = {
    "/fonts.css",
    "/tokens.css",
    "/theme.js",
    "/fonts/geist-latin-wght-normal.woff2",
}


def test_the_landing_loads_only_public_static_assets_and_links_to_the_app() -> None:
    # The static files (fonts, tokens, theme) are public: the proxy only protects learner pages and
    # APIs with account sessions. The landing may therefore self-host its fonts and share the
    # tokens of the app, and it must not call any third party (no Google Fonts: GDPR).
    html = LANDING.read_text(encoding="utf-8")
    local = {path.split("?")[0] for path in re.findall(r'(?:src|href)="(/[^"]*)"', html)}
    assert "/app" in local
    assert local - {"/app"} == PUBLIC_LANDING_ASSETS
    assert "googleapis" not in html and "gstatic" not in html
    assert not re.findall(r'(?:src|href)="https?://', html)


def test_the_landing_assets_are_served_without_a_session(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        for path in sorted(PUBLIC_LANDING_ASSETS):
            assert client.get(path).status_code == 200, path


def test_the_back_office_self_hosts_its_fonts_and_has_the_theme_switch() -> None:
    root = Path(__file__).resolve().parents[2] / "backoffice-web"
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "googleapis" not in html and "gstatic" not in html
    assert 'src="/theme.js' in html and "data-theme-toggle" in html
    assert (root / "fonts" / "geist-latin-wght-normal.woff2").exists()
    # One theme script for both origins: the two copies must stay identical.
    assert (root / "theme.js").read_text() == (root.parent / "web" / "theme.js").read_text()
