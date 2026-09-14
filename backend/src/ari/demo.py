"""Run the offline MVP against a fresh temporary database seeded with synthetic demo content."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from alembic import command
from alembic.config import Config
from pydantic_settings import SettingsConfigDict

from ari.api.app import create_app
from ari.config import PROJECT_ROOT, Settings
from ari.domain.clinical import CaseReview, ClinicalBundle, VersionRef
from ari.domain.models import new_id
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.yaml_io import parse_bundle
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository

DEMO_BUNDLES = PROJECT_ROOT / "cases" / "demo"
DEMO_ACTOR = "ari.demo (simulated review of synthetic fiction, not a human approval)"


class DemoSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="ARI_", extra="ignore")


def publish_demo_content(store: ClinicalStore, bundle: ClinicalBundle) -> None:
    """Same import → review → publish path as real content, with simulated reviews."""
    store.import_bundle(bundle)
    for scenario in bundle.scenarios:
        case = next(
            c
            for c in bundle.cases
            if (c.id, c.version) == (scenario.case.id, scenario.case.version)
        )
        for kind in ("clinical", "linguistic"):
            store.record_review(
                CaseReview(
                    id=new_id(),
                    case=scenario.case,
                    case_hash=case.content_hash,
                    scenario=VersionRef(id=scenario.id, version=scenario.version),
                    scenario_hash=scenario.content_hash,
                    review_type=kind,  # type: ignore[arg-type]
                    reviewer_name=DEMO_ACTOR,
                    reviewed_at=datetime.now(UTC),
                    decision="approve",
                    notes="Synthetic demonstration content; no clinical or linguistic validity.",
                )
            )
        store.publish(scenario.id, scenario.version, actor=DEMO_ACTOR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ari-mvp-demo-") as directory:
        database_url = f"sqlite:///{Path(directory) / 'demo.db'}"
        config = Config(PROJECT_ROOT / "alembic.ini")
        config.attributes["database_url"] = database_url
        command.upgrade(config, "head")
        store = ClinicalStore(SqliteSessionRepository(database_url).engine)
        for path in sorted(DEMO_BUNDLES.glob("*.yaml")):
            publish_demo_content(store, parse_bundle(path.read_text(encoding="utf-8")))
        settings = DemoSettings(
            environment="development", provider_mode="fake", database_url=database_url
        )
        uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
