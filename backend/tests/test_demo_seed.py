"""Demo and dev bundles publish through the real import/review/publish path and stay synthetic."""

import hashlib

from conftest import build_test_container

from ari.demo import DEMO_BUNDLES, DEV_BUNDLES, publish_demo_content, seed_bundles
from ari.infrastructure.cases.yaml_io import bundle_kind, parse_bundle, parse_placement_bundle

GOLD_PROTOCOL = DEV_BUNDLES / "ari_dev_fr_gold_protocol.v1.yaml"


def test_demo_directory_holds_only_the_german_placement_set(database_url: str) -> None:
    container = build_test_container(database_url)
    kinds = {
        path.name: bundle_kind(path.read_text(encoding="utf-8"))
        for path in sorted(DEMO_BUNDLES.glob("*.yaml"))
    }
    assert kinds == {"ari_placement_demo.v1.yaml": "ari-placement-bundle-v1"}
    placement = parse_placement_bundle((DEMO_BUNDLES / "ari_placement_demo.v1.yaml").read_text())
    assert all(source.synthetic for source in placement.sources)
    seed_bundles(container.cases.store, DEMO_BUNDLES)
    published = container.placement.store.published()
    assert published is not None and published.id == "ari-placement-demo"
    assert container.cases.list() == ()


def test_new_case_version_supersedes_the_older_one_in_a_kept_database(database_url: str) -> None:
    from clinical_fixtures import synthetic_bundle

    container = build_test_container(database_url)
    publish_demo_content(container.cases.store, synthetic_bundle("1"))
    publish_demo_content(container.cases.store, synthetic_bundle("2"))
    listed = container.cases.list()
    assert [(case.id, case.version) for case in listed] == [("SYNTHETIC-TEST", "2")]
    # The old scenario is withdrawn, not deleted: pinned sessions still resolve it.
    old = container.cases.get("SYNTHETIC-TEST", "1")
    assert old.validation_status == "withdrawn" and not old.available_for_new_sessions


def test_dev_bundle_is_a_french_case_with_two_exercises_and_reseeding_is_a_no_op(
    database_url: str,
) -> None:
    container = build_test_container(database_url)
    gold_hash = hashlib.sha256(GOLD_PROTOCOL.read_bytes()).hexdigest()
    for path in sorted(DEV_BUNDLES.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if bundle_kind(text) != "ari-clinical-bundle-v1":
            continue
        bundle = parse_bundle(text)
        assert all(source.synthetic for source in bundle.sources)
        # The dev case traces to the synthetic gold protocol file next to it.
        assert bundle.cases[0].gold_protocol.protocol_hash == gold_hash
        assert bundle.cases[0].location.land == "Bayern"
    seed_bundles(container.cases.store, DEV_BUNDLES)
    dev_placement = container.placement.store.published()
    assert dev_placement is not None and dev_placement.language == "fr-FR"
    seed_bundles(container.cases.store, DEV_BUNDLES)  # persistent dev database, second launch
    cases = container.cases.list()
    assert [(case.id, case.language) for case in cases] == [("ARI-DEV-FR", "fr-FR")]
    assert cases[0].facts and all(fact.patient_phrase for fact in cases[0].facts)
    exercises = sorted(c.scenario_id for c in container.practice.catalog.list())
    assert exercises == ["ari-dev-fr-arzt_arzt", "ari-dev-fr-fachbegriffe"]
    # Phase 2 pedagogy travels with the dev scenario.
    assert [m.fact_id for m in cases[0].empathy_moments] == ["family_history"]
    assert {s.id for s in cases[0].anamnesis_sections} >= {"aktuelle_beschwerden", "noxen"}
