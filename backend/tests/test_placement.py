"""Placement test: registry, deterministic staircase, full flow in fake mode, profile update."""

from __future__ import annotations

import json
import wave
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ari.api.app import create_app
from ari.container import Container
from ari.demo import DEMO_BUNDLES, DEV_BUNDLES, publish_placement_content
from ari.domain.errors import InvalidStateError
from ari.domain.placement import (
    LEVELS,
    PlacementBundle,
    band,
    estimate_level,
    listening_level,
    next_level,
    shift_level,
)
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.cases.yaml_io import parse_placement_bundle

DEMO_PLACEMENT = DEMO_BUNDLES / "ari_placement_demo.v1.yaml"


def demo_placement() -> PlacementBundle:
    return parse_placement_bundle(DEMO_PLACEMENT.read_text(encoding="utf-8"))


@pytest.fixture
def placement_container(published_container: Container) -> Container:
    publish_placement_content(
        PlacementStore(published_container.repository.engine), demo_placement()
    )
    return published_container


# ----- deterministic rules ------------------------------------------------------------


def test_staircase_moves_after_two_consecutive_answers_within_available_levels() -> None:
    available = ("A1", "A2", "B1", "B2")
    level, up, down, direction = next_level("A2", True, 0, 0, available)
    assert (level, up, down, direction) == ("A2", 1, 0, 0)
    level, up, down, direction = next_level("A2", True, 1, 0, available)
    assert (level, up, down, direction) == ("B1", 0, 0, 1)
    level, up, down, direction = next_level("B1", False, 0, 1, available)
    assert (level, up, down, direction) == ("A2", 0, 0, -1)
    # At the floor, two wrong answers stay at A1 without counting a direction change.
    assert next_level("A1", False, 0, 1, available) == ("A1", 0, 0, 0)
    assert shift_level("C2", 0, available) == "B2"


def test_estimate_is_the_low_median_of_the_last_six_levels() -> None:
    assert estimate_level([], "A2") == "A2"
    assert estimate_level(["A1", "A2", "A2", "B1", "B1", "B2", "B1", "B2"], "A1") == "B1"
    assert listening_level("B1", 4, 4, LEVELS) == "B2"
    assert listening_level("B1", 2, 4, LEVELS) == "B1"
    assert listening_level("B1", 1, 4, LEVELS) == "A2"
    assert band("B1", "B2", "A2", 0.8) == ("A2", True)
    assert band("B1", "B2", "A2", 0.4) == ("B1", False)


def test_bundle_requires_coverage_and_valid_answers() -> None:
    bundle = demo_placement()
    raw = bundle.model_dump(mode="json")
    raw["sets"][0]["mcq_items"] = [i for i in raw["sets"][0]["mcq_items"] if i["level"] != "B2"]
    with pytest.raises(ValidationError, match="Au moins 4 QCM"):
        PlacementBundle.model_validate_json(json.dumps(raw))
    raw = bundle.model_dump(mode="json")
    raw["sets"][0]["mcq_items"][0]["answer_index"] = 3
    with pytest.raises(ValidationError, match="hors des options"):
        PlacementBundle.model_validate_json(json.dumps(raw))
    dev = parse_placement_bundle((DEV_BUNDLES / "ari_placement_dev.v1.yaml").read_text())
    assert dev.sets[0].language == "fr-FR"


# ----- registry -----------------------------------------------------------------------


def test_registry_publishes_only_reviewed_exact_content(container: Container) -> None:
    store = PlacementStore(container.repository.engine)
    bundle = demo_placement()
    assert store.import_bundle(bundle)["tests_nouveaux"] == 1
    assert store.import_bundle(bundle)["tests_identiques"] == 1
    assert store.published() is None
    with pytest.raises(InvalidStateError, match="Publication refusée"):
        store.publish("ari-placement-demo", "1", actor="TEST")
    publish_placement_content(store, bundle)
    published = store.published()
    assert published is not None and published.id == "ari-placement-demo"
    # Publishing again is a no-op; a second published version supersedes the first.
    publish_placement_content(store, bundle)
    successor = bundle.model_copy(
        update={"sets": (bundle.sets[0].model_copy(update={"version": "2"}),)}
    )
    publish_placement_content(
        store, PlacementBundle.model_validate_json(successor.model_dump_json())
    )
    assert store.published() is not None and store.published().version == "2"  # type: ignore[union-attr]
    assert store.get("ari-placement-demo", "1")[1] == "withdrawn"


# ----- full flow ----------------------------------------------------------------------


def _answer_all(client: TestClient, attempt: dict, correct: bool, bundle: PlacementBundle) -> dict:  # type: ignore[type-arg]
    """Answer every MCQ/listening item of the current phase, always right or always wrong."""
    answers = {
        item.id: item.answer_index
        for item in (*bundle.sets[0].mcq_items, *bundle.sets[0].listening_items)
    }
    phase = attempt["phase"]
    step = 0
    while attempt["phase"] == phase and attempt["current_item"]:
        item = attempt["current_item"]
        choice = (
            answers[item["id"]] if correct else (answers[item["id"]] + 1) % len(item["options"])
        )
        step += 1
        response = client.post(
            f"/api/placement/attempts/{attempt['id']}/answers",
            json={"event_id": f"{phase}-{step}", "item_id": item["id"], "option_index": choice},
        )
        assert response.status_code == 200, response.text
        attempt = response.json()
    return attempt


def test_full_placement_flow_updates_the_profile(placement_container: Container) -> None:
    bundle = demo_placement()
    app = create_app(placement_container)
    with TestClient(app) as client:
        profile = client.post(
            "/api/learners",
            json={"target_cefr": "B2", "details": {"declared_level": "A2", "minutes_per_day": 20}},
        ).json()
        assert profile["details"]["declared_level"] == "A2"
        assert profile["details"]["estimated_level"] is None
        overview = client.get("/api/placement").json()
        assert overview["available"] is True and overview["latest"] is None
        assert overview["set"]["language"] == "de-DE"

        started = client.post("/api/placement/attempts", json={"request_id": "req-1"})
        assert started.status_code == 201
        attempt = started.json()
        assert attempt["phase"] == "mcq" and attempt["current_item"]["kind"] == "mcq"
        assert "answer_index" not in attempt["current_item"]
        # Replaying the start request returns the same attempt.
        assert (
            client.post("/api/placement/attempts", json={"request_id": "req-1"}).json()["id"]
            == attempt["id"]
        )

        attempt = _answer_all(client, attempt, correct=True, bundle=bundle)
        assert attempt["phase"] == "listening"
        assert attempt["progress"]["mcq_answered"] <= 14
        # Audio is only served for items the learner reached, as WAV.
        item = attempt["current_item"]
        audio = client.get(f"/api/placement/attempts/{attempt['id']}/items/{item['id']}/audio")
        assert audio.status_code == 200 and audio.headers["content-type"].startswith("audio/wav")
        with wave.open(BytesIO(audio.content)) as container:
            assert container.getframerate() == 24_000 and container.getnframes() > 0
        assert (
            client.get(f"/api/placement/attempts/{attempt['id']}/items/a1-1/audio").status_code
            == 404
        )

        attempt = _answer_all(client, attempt, correct=True, bundle=bundle)
        assert attempt["phase"] == "speaking"
        speaking = attempt["current_item"]
        long_answer = " ".join(["Ich arbeite als Ärztin und lerne Deutsch."] * 8)
        first = client.post(
            f"/api/placement/attempts/{attempt['id']}/speaking/{speaking['id']}",
            content=long_answer,
            headers={"content-type": "text/plain", "x-event-id": "speak-1"},
        )
        assert first.status_code == 200, first.text
        attempt = first.json()
        assert attempt["phase"] == "speaking" and attempt["current_item"]["id"] != speaking["id"]
        # Replay with the same event id is a no-op.
        replay = client.post(
            f"/api/placement/attempts/{attempt['id']}/speaking/{speaking['id']}",
            content=long_answer,
            headers={"content-type": "text/plain", "x-event-id": "speak-1"},
        )
        assert replay.json()["progress"]["speaking_answered"] == 1
        second = client.post(
            f"/api/placement/attempts/{attempt['id']}/speaking/{attempt['current_item']['id']}",
            content=long_answer,
            headers={"content-type": "text/plain", "x-event-id": "speak-2"},
        ).json()
        assert second["status"] == "completed" and second["phase"] == "completed"
        result = second["result"]
        assert result["method"] == "placement-staircase-v1"
        assert result["vocab_grammar_level"] == "B2"  # all correct: the staircase climbs to the top
        assert result["listening_level"] == "B2"
        assert result["speaking_level"] == "B2" and result["speaking_counted"] is True
        assert result["band"] == "B2"
        assert len(second["speaking_feedback"]) == 2

        profile = client.get("/api/profile").json()
        assert profile["details"]["estimated_level"] == "B2"
        assert profile["details"]["placement_attempt_id"] == attempt["id"]
        overview = client.get("/api/placement").json()
        assert overview["latest"]["status"] == "completed" and overview["estimated_level"] == "B2"


def test_wrong_answers_descend_and_early_finish_ignores_missing_phases(
    placement_container: Container,
) -> None:
    bundle = demo_placement()
    app = create_app(placement_container)
    with TestClient(app) as client:
        client.post(
            "/api/learners", json={"target_cefr": "C1", "details": {"declared_level": "B1"}}
        )
        attempt = client.post("/api/placement/attempts", json={"request_id": "req-2"}).json()
        assert client.post(f"/api/placement/attempts/{attempt['id']}/finish").status_code == 400
        attempt = _answer_all(client, attempt, correct=False, bundle=bundle)
        assert attempt["phase"] == "listening"
        finished = client.post(f"/api/placement/attempts/{attempt['id']}/finish").json()
        assert finished["status"] == "completed"
        assert finished["result"]["vocab_grammar_level"] == "A1"
        assert finished["result"]["speaking_level"] is None
        assert finished["result"]["band"] == "A1"
        # A question answered out of order or twice is refused.
        assert (
            client.post(
                f"/api/placement/attempts/{attempt['id']}/answers",
                json={"event_id": "late", "item_id": "a1-1", "option_index": 0},
            ).status_code
            == 400
        )


def test_placement_is_owned_and_unavailable_without_published_set(
    placement_container: Container, tmp_path: Path
) -> None:
    app = create_app(placement_container)
    with TestClient(app) as alice, TestClient(app) as bob:
        alice.post("/api/learners", json={})
        bob.post("/api/learners", json={})
        attempt = alice.post("/api/placement/attempts", json={"request_id": "req-3"}).json()
        assert bob.get(f"/api/placement/attempts/{attempt['id']}").status_code == 404
        assert (
            bob.post(
                f"/api/placement/attempts/{attempt['id']}/answers",
                json={"event_id": "x", "item_id": attempt["current_item"]["id"], "option_index": 0},
            ).status_code
            == 404
        )
        # Profile edits are owned too and never touch the estimate.
        me = alice.get("/api/profile").json()
        updated = alice.patch(
            f"/api/learners/{me['id']}/profile",
            json={"exam_date": "2027-03-01", "land": "Bayern", "specialty": "Innere Medizin"},
        ).json()
        assert updated["details"]["exam_date"] == "2027-03-01"
        assert updated["details"]["minutes_per_day"] == 30
        # A declared certificate sets the declared level; the estimate is never touched.
        certified = alice.patch(
            f"/api/learners/{me['id']}/profile",
            json={
                "level_source": "certificate",
                "certificate_issuer": "goethe",
                "certificate_level": "B2",
            },
        ).json()
        assert certified["details"]["declared_level"] == "B2"
        assert certified["details"]["certificate_issuer"] == "goethe"
        assert certified["details"]["estimated_level"] is None
        assert (
            alice.patch(
                f"/api/learners/{me['id']}/profile", json={"certificate_issuer": "cambridge"}
            ).status_code
            == 422
        )
        assert (
            bob.patch(f"/api/learners/{me['id']}/profile", json={"land": "Berlin"}).status_code
            == 404
        )
        # The working goal inside ARI stays B2 or C1: C2 is refused, even for a fresh profile.
        with TestClient(app) as fresh:
            assert fresh.post("/api/learners", json={"target_cefr": "C2"}).status_code == 422
    from conftest import build_test_container

    empty = build_test_container(tmp_path / "no-placement.db")
    with TestClient(create_app(empty)) as client:
        client.post("/api/learners", json={})
        assert client.get("/api/placement").json()["available"] is False
        assert client.post("/api/placement/attempts", json={"request_id": "r"}).status_code == 400
