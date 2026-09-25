"""Land: one closed list shared by the profile, the catalogue, the summary and the programme."""

from __future__ import annotations

from dataclasses import replace

from accounts_fixtures import sign_in
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.application.services.catalog import land_summary
from ari.container import Container
from ari.domain.geography import LAND_CODES, Land


def test_reference_list_and_codes_cover_the_sixteen_laender(published_container: Container) -> None:
    with TestClient(create_app(published_container)) as client:
        laender = client.get("/api/reference/laender").json()
    assert laender == [land.value for land in Land]
    assert len(laender) == 16 and "Bayern" in laender and "Nordrhein-Westfalen" in laender
    assert len(set(LAND_CODES.values())) == 16


def test_catalogue_exposes_the_land_and_the_profile_rejects_an_unknown_one(
    published_container: Container,
) -> None:
    with TestClient(create_app(published_container)) as client:
        case = client.get("/api/cases").json()[0]
        assert (case["land"], case["city"]) == ("Bayern", "Teststadt")
        sign_in(client)
        me = client.post("/api/learners", json={"target_cefr": "C1"}).json()
        refused = client.patch(f"/api/learners/{me['id']}/profile", json={"land": "Atlantis"})
        assert refused.status_code == 422
        updated = client.patch(f"/api/learners/{me['id']}/profile", json={"land": "Berlin"}).json()
        assert updated["details"]["land"] == "Berlin"
        assert client.get("/api/cases/summary").json() == {
            "learner_land": "Berlin",
            "laender": [{"land": "Bayern", "cases": 1, "worked": 0, "share_worked": 0.0}],
            "without_land": 0,
        }


def test_land_summary_counts_worked_cases_and_skips_withdrawn_ones(
    published_container: Container,
) -> None:
    case = published_container.cases.list()[0]
    elsewhere = replace(case, id="ELSEWHERE", land=Land.BERLIN)
    withdrawn = replace(case, id="OLD", available_for_new_sessions=False)
    nowhere = replace(case, id="NOWHERE", land=None)
    summary = land_summary((case, elsewhere, withdrawn, nowhere), {"ELSEWHERE"}, Land.BAYERN)
    assert summary["learner_land"] == "Bayern" and summary["without_land"] == 1
    assert [
        (e["land"], e["cases"], e["worked"], e["share_worked"]) for e in summary["laender"]
    ] == [("Bayern", 1, 0, 0.0), ("Berlin", 1, 1, 1.0)]
