"""The language exercises in the back-office: filters, detail, and the owner's withdrawal."""

from __future__ import annotations

from typing import Any

from clinical_fixtures import synthetic_bundle
from conftest import publish_with_simulated_reviews
from fastapi.testclient import TestClient
from practice_fixtures import synthetic_practice
from test_backoffice_api import signed_in
from test_language_exercises_domain import CLOZE, CORRECTION, REGISTER, candidate

from ari.backoffice_api.app import create_backoffice_app
from ari.backoffice_api.container import Backoffice
from ari.content.domain.accounts import Role
from ari.domain.language_exercises import ExerciseReview, LanguageExercise
from ari.infrastructure.cases.clinical_store import ClinicalStore

REVIEWS = [
    ExerciseReview("blind_solver", "anthropic:claude-sonnet-5-5@exercise-solver-v1", "approve", {}),
    ExerciseReview(
        "rubric_judge", "anthropic:claude-sonnet-5-5@exercise-rubric-v1", "approve", {"ok": True}
    ),
]


def fill(services: Backoffice) -> dict[str, LanguageExercise]:
    items = {
        name: LanguageExercise.model_validate_json(candidate(base).model_dump_json())
        for name, base in (("cloze", CLOZE), ("correction", CORRECTION), ("register", REGISTER))
    }
    for item in items.values():
        services.exercises.add(
            item, batch_id="b1", provenance={"author": {"model": "gpt-6-luna"}}, reviews=REVIEWS
        )
    return items


def fill_medical(services: Backoffice) -> None:
    """One imported (unreviewed) scenario and two published ones, as the registry holds them."""
    store = ClinicalStore(services.exercises.engine)
    store.import_bundle(synthetic_bundle())
    for phase in ("arzt_arzt", "fachbegriffe"):
        publish_with_simulated_reviews(store, synthetic_practice(phase).bundle)


def ids(response: Any) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def test_the_list_needs_an_account_and_filters_by_every_axis(backoffice: Backoffice) -> None:
    items = fill(backoffice)
    app = create_backoffice_app(backoffice)
    assert TestClient(app).get("/api/exercises").status_code == 401
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        everything = c.get("/api/exercises")
        assert ids(everything) == {i.exercise_id for i in items.values()}
        body = everything.json()
        assert body["total"] == 3
        assert body["counts"]["level"] == {"B1": 2, "B2": 1}
        assert body["counts"]["type"] == {"cloze": 1, "correct_sentence": 1, "register_choice": 1}
        assert body["counts"]["status"] == {"active": 3}
        wanted = {
            "level=B2": {items["register"].exercise_id},
            "category=case": {items["correction"].exercise_id},
            "skill=grammar": {items["cloze"].exercise_id, items["correction"].exercise_id},
            "type=cloze": {items["cloze"].exercise_id},
            "level=B1&skill=grammar&category=gender": {items["cloze"].exercise_id},
            "level=C1": set(),
        }
        for query, expected in wanted.items():
            assert ids(c.get(f"/api/exercises?{query}")) == expected, query
        assert c.get("/api/exercises?level=A1").status_code == 422
        assert c.get("/api/exercises?status=deleted").status_code == 422


def test_the_list_is_paged_and_carries_the_total(backoffice: Backoffice) -> None:
    fill(backoffice)
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        first = c.get("/api/exercises?limit=2").json()
        rest = c.get("/api/exercises?limit=2&offset=2").json()
        assert (len(first["items"]), len(rest["items"]), first["total"]) == (2, 1, 3)
        assert not {i["id"] for i in first["items"]} & {i["id"] for i in rest["items"]}
        assert c.get("/api/exercises?limit=0").status_code == 422


def test_the_detail_shows_the_exercise_its_key_its_provenance_and_its_checks(
    backoffice: Backoffice,
) -> None:
    items = fill(backoffice)
    item = items["cloze"]
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        detail = c.get(f"/api/exercises/{item.exercise_id}").json()
        assert detail["options"] == list(item.options)
        assert detail["options"][detail["answer_index"]] == item.key
        assert detail["correct_sentence"] == item.correct_sentence()
        assert detail["explanation_fr"] == item.explanation_fr
        assert detail["instruction_fr"] == item.instruction_fr
        assert detail["provenance"] == {"author": {"model": "gpt-6-luna"}}
        assert [r["kind"] for r in detail["reviews"]] == ["blind_solver", "rubric_judge"]
        assert c.get("/api/exercises/lx-unknown").status_code == 404


def test_a_long_stem_is_cut_in_the_list_but_whole_in_the_detail(backoffice: Backoffice) -> None:
    long_stem = "Der Arzt erklärt ___ Patienten " + "sehr ausführlich " * 12 + "die Untersuchung."
    item = LanguageExercise.model_validate_json(candidate(CLOZE, stem=long_stem).model_dump_json())
    backoffice.exercises.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        listed = c.get("/api/exercises").json()["items"][0]["stem"]
        assert len(listed) <= 120 and listed.endswith("…")
        assert c.get(f"/api/exercises/{item.exercise_id}").json()["stem"] == long_stem


def test_only_the_owner_withdraws_and_the_withdrawal_is_final_and_traced(
    backoffice: Backoffice,
) -> None:
    items = fill(backoffice)
    item = items["cloze"]
    app = create_backoffice_app(backoffice)
    path = f"/api/exercises/{item.exercise_id}/withdraw"
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        assert c.post(path, json={"reason": "Deux réponses possibles"}).status_code == 403
    with signed_in(app, backoffice, "o@example.org", "Ismail", Role.OWNER) as c:
        assert c.post(path, json={"reason": "ab"}).status_code == 422
        done = c.post(path, json={"reason": "Deux réponses possibles"})
        assert done.status_code == 200 and done.json()["status"] == "withdrawn"
        assert done.json()["withdrawn_reason"] == "Deux réponses possibles"
        assert c.post(path, json={"reason": "encore"}).status_code == 400  # Already withdrawn.
        assert (
            c.post("/api/exercises/lx-unknown/withdraw", json={"reason": "abc"}).status_code == 404
        )
        assert ids(c.get("/api/exercises?status=withdrawn")) == {item.exercise_id}
        assert item.exercise_id not in ids(c.get("/api/exercises?status=active"))
        trace = c.get(f"/api/exercises/{item.exercise_id}").json()["reviews"][-1]
        assert (trace["kind"], trace["name"], trace["decision"]) == ("human", "Ismail", "reject")
        counts = c.get("/api/exercises").json()["counts"]["status"]
        assert counts == {"active": 2, "withdrawn": 1}


def test_a_foreign_origin_cannot_withdraw(backoffice: Backoffice) -> None:
    item = fill(backoffice)["cloze"]
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "o@example.org", "Ismail", Role.OWNER) as c:
        forged = c.post(
            f"/api/exercises/{item.exercise_id}/withdraw",
            json={"reason": "Deux réponses possibles"},
            headers={"Origin": "https://evil.example"},
        )
        assert forged.status_code == 403
        assert backoffice.exercises.get(item.exercise_id).status == "active"


def test_the_list_holds_the_medical_scenarios_too_with_the_same_type_filter(
    backoffice: Backoffice,
) -> None:
    language = fill(backoffice)
    fill_medical(backoffice)
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        body = c.get("/api/exercises").json()
        by_kind = {
            kind: [i for i in body["items"] if i["kind"] == kind]
            for kind in ("medical", "language")
        }
        assert len(by_kind["medical"]) == 3 and len(by_kind["language"]) == 3
        assert body["total"] == 6
        assert [i["kind"] for i in body["items"]][:3] == ["medical"] * 3  # Medical first.
        assert body["counts"]["type"] == {
            "cloze": 1,
            "correct_sentence": 1,
            "register_choice": 1,
            "arzt_patient": 1,
            "arzt_arzt": 1,
            "fachbegriffe": 1,
        }
        assert body["counts"]["status"] == {"active": 5, "draft_unvalidated": 1}
        medical = {i["type"]: i for i in by_kind["medical"]}
        assert medical["arzt_patient"]["status"] == "draft_unvalidated"
        assert medical["arzt_arzt"]["status"] == "active"  # Published reads as active.
        assert medical["arzt_arzt"]["version"] and medical["arzt_arzt"]["level"] is None
        wanted = {
            "type=arzt_arzt": {medical["arzt_arzt"]["id"]},
            "type=fachbegriffe": {medical["fachbegriffe"]["id"]},
            "type=cloze": {language["cloze"].exercise_id},
            "status=draft_unvalidated": {medical["arzt_patient"]["id"]},
        }
        for query, expected in wanted.items():
            assert ids(c.get(f"/api/exercises?{query}")) == expected, query
        # Level, category and skill describe language exercises only.
        assert not [
            i for i in c.get("/api/exercises?level=B1").json()["items"] if i["kind"] == "medical"
        ]
        assert c.get("/api/exercises?type=cloze&level=B1").json()["total"] == 1


def test_paging_runs_through_both_kinds_without_gaps_or_repeats(backoffice: Backoffice) -> None:
    fill(backoffice)
    fill_medical(backoffice)
    app = create_backoffice_app(backoffice)
    with signed_in(app, backoffice, "r@example.org", "Relecteur", Role.LINGUISTIC_REVIEWER) as c:
        everything = [i["id"] for i in c.get("/api/exercises").json()["items"]]
        for size in (1, 2, 4):
            seen: list[str] = []
            for offset in range(0, 6, size):
                page = c.get(f"/api/exercises?limit={size}&offset={offset}").json()
                assert page["total"] == 6
                seen += [i["id"] for i in page["items"]]
            assert seen == everything, size
