"""Demo and dev bundles publish through the real import/review/publish path and stay synthetic."""

from pathlib import Path

from conftest import build_test_container

from ari.demo import DEMO_BUNDLES, DEV_BUNDLES, publish_demo_content, seed_bundles
from ari.infrastructure.cases.yaml_io import bundle_kind, parse_bundle, parse_placement_bundle


def test_demo_bundle_publishes_one_voice_case_and_two_exercises(tmp_path: Path) -> None:
    container = build_test_container(tmp_path / "demo.db")
    for path in sorted(DEMO_BUNDLES.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if bundle_kind(text) == "ari-placement-bundle-v1":
            placement = parse_placement_bundle(text)
            assert all(source.source_type == "synthetic" for source in placement.sources)
            continue
        bundle = parse_bundle(text)
        assert all(source.source_type == "synthetic" for source in bundle.sources)
        publish_demo_content(container.cases.store, bundle)
    assert [case.id for case in container.cases.list()] == ["ARI-DEMO"]
    exercises = sorted(c.scenario_id for c in container.practice.catalog.list())
    assert exercises == ["ari-demo-arzt_arzt", "ari-demo-fachbegriffe"]
    # The placement set goes through the same seeding entry point.
    seed_bundles(container.cases.store, DEMO_BUNDLES)
    published = container.placement.store.published()
    assert published is not None and published.id == "ari-placement-demo"


def test_new_case_version_supersedes_the_older_one_in_a_kept_database(tmp_path: Path) -> None:
    from clinical_fixtures import synthetic_bundle

    container = build_test_container(tmp_path / "dev-evolving.db")
    publish_demo_content(container.cases.store, synthetic_bundle("1"))
    publish_demo_content(container.cases.store, synthetic_bundle("2"))
    listed = container.cases.list()
    assert [(case.id, case.version) for case in listed] == [("SYNTHETIC-TEST", "2")]
    # The old scenario is withdrawn, not deleted: pinned sessions still resolve it.
    old = container.cases.get("SYNTHETIC-TEST", "1")
    assert old.validation_status == "withdrawn" and not old.available_for_new_sessions


def test_dev_bundle_is_a_french_voice_case_and_reseeding_a_kept_database_is_a_no_op(
    tmp_path: Path,
) -> None:
    container = build_test_container(tmp_path / "dev.db")
    for path in sorted(DEV_BUNDLES.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if bundle_kind(text) == "ari-placement-bundle-v1":
            continue
        bundle = parse_bundle(text)
        assert all(source.source_type == "synthetic" for source in bundle.sources)
    seed_bundles(container.cases.store, DEV_BUNDLES)
    dev_placement = container.placement.store.published()
    assert dev_placement is not None and dev_placement.language == "fr-FR"
    seed_bundles(container.cases.store, DEV_BUNDLES)  # persistent dev database, second launch
    cases = container.cases.list()
    assert [(case.id, case.language) for case in cases] == [("ARI-DEV-FR", "fr-FR")]
    assert cases[0].facts and all(fact.patient_phrase for fact in cases[0].facts)
    assert container.practice.catalog.list() == ()
    # Phase 2 pedagogy travels with the dev scenario.
    assert [m.fact_id for m in cases[0].empathy_moments] == ["family_history"]
    assert {s.id for s in cases[0].anamnesis_sections} >= {"aktuelle_beschwerden", "noxen"}
