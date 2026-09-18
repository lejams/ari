"""Run ARI against synthetic content published through the real registry path.

Default: fake providers, the PostgreSQL platform database from `ARI_DATABASE_URL` (or the
docker compose default), `cases/demo`. Development against live providers, for example a
French voice case so the team can test the platform without speaking German:

    python -m ari.demo --provider openai --bundles cases/dev

Reviews are simulated: none of this content is approved for learners. Re-running against
the same database is a no-op for identical content.
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import uvicorn
from pydantic_settings import SettingsConfigDict
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from ari.api.app import create_app
from ari.config import PROJECT_ROOT, Settings
from ari.domain.clinical import CaseReview, ClinicalBundle, TrainingScenarioVersion, VersionRef
from ari.domain.models import new_id
from ari.domain.placement import PlacementBundle, PlacementReview
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.cases.yaml_io import bundle_kind, parse_bundle, parse_placement_bundle
from ari.infrastructure.persistence.platform import Base, create_platform_engine
from ari.infrastructure.persistence.platform.clinical_rows import ScenarioRow
from ari.infrastructure.persistence.platform.schema import upgrade_to_head

DEMO_BUNDLES = PROJECT_ROOT / "cases" / "demo"
DEV_BUNDLES = PROJECT_ROOT / "cases" / "dev"
DEMO_ACTOR = "ari.demo (simulated review of synthetic fiction, not a human approval)"


class DemoSettings(Settings):
    """Hermetic: never reads `.env`."""

    model_config = SettingsConfigDict(env_file=None, env_prefix="ARI_", extra="ignore")


def publish_demo_content(store: ClinicalStore, bundle: ClinicalBundle) -> None:
    """Same import → review → publish path as real content, with simulated reviews.

    Re-running on a persistent database is a no-op: identical content re-imports
    silently and scenarios that already left the draft state are skipped.
    """
    store.import_bundle(bundle)
    for scenario in bundle.scenarios:
        if store.inspect(scenario.id, scenario.version)["status"] != "draft_unvalidated":
            continue
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
        _withdraw_older_case_versions(store, scenario)


def _withdraw_older_case_versions(store: ClinicalStore, scenario: TrainingScenarioVersion) -> None:
    """Demo convenience: a new case version supersedes the older one in the catalogue.

    The registry only withdraws a predecessor for the same case version. In a persistent
    development database an edited case would otherwise appear twice; sessions pinned
    to the old scenario keep working through their pin.
    """
    with Session(store.engine) as db:
        rows = db.scalars(
            select(ScenarioRow).where(
                ScenarioRow.case_id == scenario.case.id,
                ScenarioRow.phase == scenario.phase,
                ScenarioRow.status == "published",
                ScenarioRow.case_version != scenario.case.version,
            )
        ).all()
        older = [(row.id, row.version) for row in rows]
    for scenario_id, version in older:
        store.withdraw(scenario_id, version, actor=DEMO_ACTOR)


def _ensure_schema(engine: Engine, database_url: str) -> None:
    """The single revision is regenerated while no real database exists (docs/MIGRATIONS.md).

    A persistent development database created by an older `0001_initial` reports itself as
    current yet lacks the newer tables; fail early with the remedy instead of at first use.
    """
    missing = set(Base.metadata.tables) - set(inspect(engine).get_table_names())
    if missing:
        raise SystemExit(
            f"Base {database_url} créée par une ancienne révision (tables manquantes : "
            f"{', '.join(sorted(missing))}). Recréez-la (`make db-reset`) et relancez : la démo "
            "republie le contenu synthétique."
        )


def publish_placement_content(store: PlacementStore, bundle: PlacementBundle) -> None:
    """Placement sets need one linguistic approval; here it is simulated, like the cases."""
    store.import_bundle(bundle)
    for placement_set in bundle.sets:
        if store.inspect(placement_set.id, placement_set.version)["status"] != "draft_unvalidated":
            continue
        store.record_review(
            PlacementReview(
                id=new_id(),
                set=VersionRef(id=placement_set.id, version=placement_set.version),
                set_hash=placement_set.content_hash,
                reviewer_name=DEMO_ACTOR,
                reviewed_at=datetime.now(UTC),
                decision="approve",
                notes="Synthetic placement items; no linguistic validity claimed.",
            )
        )
        store.publish(placement_set.id, placement_set.version, actor=DEMO_ACTOR)


def seed_bundles(store: ClinicalStore, directory: Path) -> None:
    placement_store = PlacementStore(store.engine)
    for path in sorted(directory.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if bundle_kind(text) == "ari-placement-bundle-v1":
            publish_placement_content(placement_store, parse_placement_bundle(text))
        else:
            publish_demo_content(store, parse_bundle(text))


def serve(settings: Settings, bundles: Path, port: int, database_url: str | None = None) -> None:
    if database_url is not None:
        settings = settings.model_copy(update={"database_url": database_url})
    upgrade_to_head(settings.database_url)
    engine = create_platform_engine(settings.database_url)
    _ensure_schema(engine, settings.database_url)
    seed_bundles(ClinicalStore(engine), bundles)
    engine.dispose()
    uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--provider", choices=["fake", "openai"], default="fake")
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL URL. Default: ARI_DATABASE_URL, else the compose platform database.",
    )
    parser.add_argument(
        "--bundles",
        type=Path,
        default=DEMO_BUNDLES,
        help="Directory of YAML bundles published with simulated reviews.",
    )
    args = parser.parse_args()
    provider: Literal["fake", "openai"] = args.provider
    # Live providers read their keys from `.env`; the fake demo never reads that file.
    settings_class: type[Settings] = Settings if provider == "openai" else DemoSettings
    settings = settings_class(environment="development", provider_mode=provider)
    serve(settings, args.bundles, args.port, database_url=args.database_url)


if __name__ == "__main__":
    main()
