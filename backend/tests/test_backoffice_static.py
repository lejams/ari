"""Browser caches must revalidate the back-office UI after a release."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ari.backoffice_api.app import BackofficeStaticFiles


@pytest.mark.parametrize(
    "filename",
    ["index.html", "app.mjs", "api.mjs", "protocol-review.mjs", "review.mjs", "styles.css"],
)
def test_static_upgrade_revalidates_old_assets(tmp_path: Path, filename: str) -> None:
    asset = tmp_path / filename
    asset.write_text("old release", encoding="utf-8")
    app = FastAPI()
    app.mount("/", BackofficeStaticFiles(directory=tmp_path, html=True))
    url = "/" if filename == "index.html" else f"/{filename}"

    with TestClient(app) as client:
        original = client.get(url)
        assert original.status_code == 200
        assert original.headers["cache-control"] == "no-cache"
        etag = original.headers["etag"]

        unchanged = client.get(url, headers={"If-None-Match": etag})
        assert unchanged.status_code == 304
        assert unchanged.headers["cache-control"] == "no-cache"

        asset.write_text("new release with protocolSource", encoding="utf-8")
        upgraded = client.get(url, headers={"If-None-Match": etag})
        assert upgraded.status_code == 200
        assert upgraded.text == "new release with protocolSource"
        assert upgraded.headers["etag"] != etag
        assert upgraded.headers["cache-control"] == "no-cache"
