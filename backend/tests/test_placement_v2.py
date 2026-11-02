"""Placement test v2 (B1 to C1): set contract, categories, weak points, spoken errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from accounts_fixtures import sign_in
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ari.api.app import create_app
from ari.container import Container
from ari.demo import publish_placement_content
from ari.domain.placement import (
    METHOD_V2,
    PlacementBundle,
    PlacementSetVersion,
    weak_points,
)
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.cases.yaml_io import parse_placement_bundle
from ari.infrastructure.persistence.platform.placement_rows import PlacementEventRow

ROOT = Path(__file__).resolve().parents[2]
GERMAN_SET = ROOT / "cases" / "placement" / "ari_placement_de.v2.yaml"
DEMO_SET = ROOT / "cases" / "demo" / "ari_placement_demo.v1.yaml"


def german_bundle() -> PlacementBundle:
    return parse_placement_bundle(GERMAN_SET.read_text(encoding="utf-8"))


@pytest.fixture
def v2_container(published_container: Container) -> Container:
    publish_placement_content(
        PlacementStore(published_container.repository.engine), german_bundle()
    )
    return published_container


def mutated(change) -> dict:  # type: ignore[no-untyped-def,type-arg]
    raw = german_bundle().model_dump(mode="json")
    change(raw["sets"][0])
    return raw


# ----- the set contract ---------------------------------------------------------------


def test_german_set_is_a_valid_v2_set() -> None:
    placement_set = german_bundle().sets[0]
    assert placement_set.schema_version == "placement-set-v2"
    assert placement_set.method is METHOD_V2
    assert {i.level for i in placement_set.mcq_items} == {"A2", "B1", "B2", "C1"}
    assert all(i.category for i in placement_set.mcq_items)
    # Each key is the only correct option in the set's own terms: one gap, distinct options.
    for item in placement_set.mcq_items:
        assert item.stem.count("___") == 1
        assert len(set(item.options)) == len(item.options)
    # The answer is not always in the same place.
    assert len({i.answer_index for i in placement_set.mcq_items}) == 3


def test_demo_set_hash_does_not_change_with_categories() -> None:
    """v1 sets published before categories existed must keep reading: no new key in their hash."""
    demo = parse_placement_bundle(DEMO_SET.read_text(encoding="utf-8")).sets[0]
    assert all("category" not in item for item in demo.model_dump(mode="json")["mcq_items"])
    assert demo.schema_version == "placement-set-v1" and demo.method.method.endswith("-v1")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda s: s["mcq_items"].__setitem__(0, {**s["mcq_items"][0], "category": None}),
            "catégorie requise",
        ),
        (
            lambda s: s["mcq_items"].__setitem__(0, {**s["mcq_items"][0], "stem": "Pas de trou."}),
            "un trou",
        ),
        (lambda s: s["mcq_items"].__setitem__(0, {**s["mcq_items"][0], "level": "A1"}), "A2 à C1"),
        (
            lambda s: s.__setitem__(
                "mcq_items", [i for i in s["mcq_items"] if i["level"] != "C1"][:30]
            ),
            "QCM requis au niveau C1",
        ),
        (
            lambda s: s.__setitem__(
                "listening_items", [i for i in s["listening_items"] if i["id"] != "l-c1-2"]
            ),
            "écoute requis au niveau C1",
        ),
        (
            lambda s: s["mcq_items"].__setitem__(
                0, {**s["mcq_items"][0], "options": ["dem", "Dem", "des"]}
            ),
            "options en double",
        ),
    ],
)
def test_v2_set_rejects_what_the_rules_forbid(change, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError, match=message):
        PlacementBundle.model_validate_json(json.dumps(mutated(change)))


def test_v2_set_needs_enough_items_per_category() -> None:
    def retag(s: dict) -> None:  # type: ignore[type-arg]
        for item in s["mcq_items"]:
            if item["category"] == "register":
                item["category"] = "case"

    with pytest.raises(ValidationError, match="catégorie register"):
        PlacementSetVersion.model_validate_json(json.dumps(mutated(retag)["sets"][0]))


# ----- weak points: pure rules --------------------------------------------------------


def test_weak_points_come_from_written_and_verified_spoken_errors() -> None:
    written = [
        ("case", False),
        ("case", False),
        ("case", True),
        ("gender", True),
        ("gender", False),
        ("verb_form", True),
        ("register", False),
    ]
    spoken = [
        {"category": "verb_form", "quote": "ich habe gegangen", "correction": "ich bin gegangen"},
        {"category": "verb_form", "quote": "er hat gefahren", "correction": "er ist gefahren"},
        {"category": "other", "quote": "x", "correction": "y"},
    ]
    points = weak_points(written, spoken)
    # case: 2 of 3 missed. gender: 1 of 2 missed. register: one item, not enough. verb_form: spoken.
    assert [p["category"] for p in points] == ["case", "verb_form", "gender"]
    case = points[0]
    assert case["signals"] == ["written"] and case["written_wrong"] == 2
    verb = points[1]
    assert verb["signals"] == ["spoken"] and verb["examples"][0]["correction"] == "ich bin gegangen"


def test_weak_points_are_capped_and_empty_when_nothing_is_established() -> None:
    assert weak_points([("case", False)], []) == []  # a single item proves nothing
    many = [(c, False) for c in ("gender", "case", "verb_form", "word_order") for _ in range(2)]
    assert len(weak_points(many, [])) == 3
    # A category flagged on both sources comes before the ones flagged on one.
    both = weak_points(
        [("gender", False)] * 2 + [("case", False)] * 2,
        [{"category": "case", "quote": "a", "correction": "b"}] * 2,
    )
    assert both[0]["category"] == "case" and both[0]["signals"] == ["written", "spoken"]


# ----- full flow ----------------------------------------------------------------------


def _answers(placement_set: PlacementSetVersion) -> dict[str, int]:
    return {
        item.id: item.answer_index
        for item in (*placement_set.mcq_items, *placement_set.listening_items)
    }


def _run_choices(client: TestClient, attempt: dict, correct: bool) -> dict:  # type: ignore[type-arg]
    answers = _answers(german_bundle().sets[0])
    phase, step = attempt["phase"], 0
    while attempt["phase"] == phase and attempt["current_item"]:
        item = attempt["current_item"]
        key = answers[item["id"]]
        step += 1
        response = client.post(
            f"/api/placement/attempts/{attempt['id']}/answers",
            json={
                "event_id": f"{phase}-{step}",
                "item_id": item["id"],
                "option_index": key if correct else (key + 1) % len(item["options"]),
            },
        )
        assert response.status_code == 200, response.text
        attempt = response.json()
    return attempt


def _speak(client: TestClient, attempt: dict, text: str, event: str) -> dict:  # type: ignore[type-arg]
    response = client.post(
        f"/api/placement/attempts/{attempt['id']}/speaking/{attempt['current_item']['id']}",
        content=text,
        headers={"content-type": "text/plain", "x-event-id": event},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_strong_learner_reaches_c1_in_about_ten_minutes_of_questions(
    v2_container: Container,
) -> None:
    with TestClient(create_app(v2_container)) as client:
        sign_in(client)
        client.post(
            "/api/learners", json={"target_cefr": "C1", "details": {"declared_level": "B1"}}
        )
        overview = client.get("/api/placement").json()
        assert overview["method"] == "placement-staircase-v2"
        assert overview["duration_minutes"] == 10
        assert overview["set"]["max_mcq"] == 12 and overview["set"]["listening_items"] == 2

        attempt = client.post("/api/placement/attempts", json={"request_id": "r1"}).json()
        assert attempt["current_item"]["id"].startswith("b1-")  # starts at the declared level
        attempt = _run_choices(client, attempt, correct=True)
        assert attempt["phase"] == "listening"
        assert attempt["progress"]["mcq_answered"] <= 12
        assert attempt["progress"]["listening_total"] == 2
        attempt = _run_choices(client, attempt, correct=True)
        assert attempt["phase"] == "speaking"
        clean = " ".join(["Ich arbeite gern in der Klinik und lerne jeden Tag Deutsch."] * 6)
        attempt = _speak(client, attempt, clean, "s1")
        done = _speak(client, attempt, clean, "s2")
        result = done["result"]
        assert result["method"] == "placement-staircase-v2"
        assert result["vocab_grammar_level"] == "C1" and result["listening_level"] == "C1"
        assert result["band"] == "B2"  # the weakest measured skill: the fake rates speaking B2
        assert result["weak_points"] == []
        assert client.get("/api/profile").json()["details"]["estimated_level"] == "B2"


def test_weak_points_combine_the_written_and_the_verified_spoken_errors(
    v2_container: Container,
) -> None:
    with TestClient(create_app(v2_container)) as client:
        sign_in(client)
        client.post("/api/learners", json={"target_cefr": "B2", "details": {}})
        attempt = client.post("/api/placement/attempts", json={"request_id": "r2"}).json()
        assert attempt["current_item"]["id"].startswith("b1-")  # no declared level: default B1
        attempt = _run_choices(client, attempt, correct=False)
        attempt = _run_choices(client, attempt, correct=False)
        # The quote is really in the transcript, so the code keeps the spoken error.
        speech = "Gestern ich habe gegangen zur Arbeit und dann nach Hause."
        attempt = _speak(client, attempt, speech, "s1")
        done = _speak(client, attempt, "Dem Familie geht es gut, aber ich habe gegangen.", "s2")
        result = done["result"]
        assert result["vocab_grammar_level"] == "A2"
        points = {p["category"]: p for p in result["weak_points"]}
        assert len(points) <= 3
        verb = points["verb_form"]
        assert "spoken" in verb["signals"] and verb["spoken_errors"] == 2
        assert verb["examples"] == [
            {"quote": "ich habe gegangen", "correction": "ich bin gegangen"},
            {"quote": "ich habe gegangen", "correction": "ich bin gegangen"},
        ]
        feedback = done["speaking_feedback"]
        assert feedback[0]["language_errors"][0]["category"] == "verb_form"
        assert {e["category"] for e in feedback[1]["language_errors"]} == {"gender", "verb_form"}
        # Written weak points rest on at least two answered items of the category.
        for point in result["weak_points"]:
            if "written" in point["signals"]:
                assert point["written_answered"] >= 2 and point["written_wrong"] >= 1


def test_an_invented_spoken_error_is_dropped_by_the_code(v2_container: Container) -> None:
    """The model proposes, the code decides: a quote absent from the transcript never counts."""
    from ari.application.schemas import LanguageErrorSchema, SpeakingRatingSchema
    from ari.infrastructure.providers.fake import FakeLLMProvider

    invented = {
        "estimated_level": "B1",
        "confidence": 0.9,
        "observations": [],
        "language_errors": [
            {
                "text": "Erfunden.",
                "evidence_turn_sequences": [1],
                "quote": "mich hilft",
                "correction": "mir hilft",
                "category": "case",
            }
        ],
    }
    provider = FakeLLMProvider(handlers={SpeakingRatingSchema: lambda _payload: invented})
    service = v2_container.placement
    service._llm = provider
    assert LanguageErrorSchema  # the schema the verification reads
    with TestClient(create_app(v2_container)) as client:
        sign_in(client)
        client.post("/api/learners", json={"target_cefr": "B2", "details": {}})
        attempt = client.post("/api/placement/attempts", json={"request_id": "r3"}).json()
        attempt = _run_choices(client, attempt, correct=True)
        attempt = _run_choices(client, attempt, correct=True)
        attempt = _speak(client, attempt, "Ich bin Ärztin und lerne Deutsch.", "s1")
        done = _speak(client, attempt, "Ich arbeite in einer Klinik.", "s2")
    assert all(f["language_errors"] == [] for f in done["speaking_feedback"])
    assert done["result"]["weak_points"] == []


def test_a_learner_can_flag_an_item_it_met(v2_container: Container) -> None:
    with TestClient(create_app(v2_container)) as client:
        sign_in(client)
        client.post("/api/learners", json={"target_cefr": "B2", "details": {}})
        attempt = client.post("/api/placement/attempts", json={"request_id": "r4"}).json()
        item = attempt["current_item"]["id"]
        url = f"/api/placement/attempts/{attempt['id']}/report"
        assert client.post(url, json={"item_id": "c1-8"}).status_code == 404  # not met yet
        ok = client.post(
            url, json={"item_id": item, "note": "Deux réponses me semblent possibles."}
        )
        assert ok.status_code == 200 and ok.json() == {"reported": True}
    with Session(v2_container.repository.engine) as db:
        events = [
            row.payload
            for row in db.scalars(select(PlacementEventRow))
            if row.payload["action"] == "item_report"
        ]
    assert len(events) == 1
    assert events[0]["item_id"] == item and events[0]["note"].startswith("Deux réponses")
    assert events[0]["set_hash"] == german_bundle().sets[0].content_hash


def test_registry_lists_what_the_server_serves(v2_container: Container) -> None:
    store = PlacementStore(v2_container.repository.engine)
    listing = store.list_sets()
    assert [(s["id"], s["status"], s["schema_version"]) for s in listing] == [
        ("ari-placement-de", "published", "placement-set-v2")
    ]
