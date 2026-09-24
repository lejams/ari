"""Web pages: the public landing owns "/", the learner app lives at "/app"."""

from __future__ import annotations

import re
from pathlib import Path

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

        learner = client.get("/app")
        assert learner.status_code == 200
        assert 'id="onboarding"' in learner.text

        # The app's assets keep their absolute paths under the static mount.
        assert client.get("/practice-ui.mjs").status_code == 200
        assert client.get("/voice.html").status_code == 200

        # Every page and module is revalidated, so a deploy is never hidden by a stale copy.
        for path in ("/", "/app", "/voice.html", "/practice-ui.mjs", "/app.js"):
            assert client.get(path).headers["cache-control"] == "no-cache", path


def test_the_landing_loads_no_local_asset_and_only_links_to_the_app() -> None:
    # Only "/" is public at the proxy: a local stylesheet, script or image would sit behind
    # basic auth and break the page for visitors.
    local = re.findall(r'(?:src|href)="(/[^"]*)"', LANDING.read_text(encoding="utf-8"))
    assert local
    assert set(local) == {"/app"}
