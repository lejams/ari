"""The demo bundle publishes through the real import/review/publish path and stays synthetic."""

from pathlib import Path

from conftest import build_test_container

from ari.demo import DEMO_BUNDLES, publish_demo_content
from ari.infrastructure.cases.yaml_io import parse_bundle


def test_demo_bundle_publishes_one_voice_case_and_two_exercises(tmp_path: Path) -> None:
    container = build_test_container(tmp_path / "demo.db")
    for path in sorted(DEMO_BUNDLES.glob("*.yaml")):
        bundle = parse_bundle(path.read_text(encoding="utf-8"))
        assert all(source.source_type == "synthetic" for source in bundle.sources)
        publish_demo_content(container.cases.store, bundle)
    assert [case.id for case in container.cases.list()] == ["ARI-DEMO"]
    exercises = sorted(c.scenario_id for c in container.practice.catalog.list())
    assert exercises == ["ari-demo-arzt_arzt", "ari-demo-fachbegriffe"]
