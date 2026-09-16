"""Run ARI against synthetic content published through the real registry path.

Default: offline demo, fake providers, temporary database, `cases/demo`.
Development against live providers, for example a French voice case so the team can
test the platform without speaking German:

    python -m ari.demo --provider openai --database var/dev-fr.db --bundles cases/dev

Reviews are simulated: none of this content is approved for learners.
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import uvicorn
from alembic import command
from alembic.config import Config
from pydantic_settings import SettingsConfigDict
from sqlalchemy import Engine, inspect

from ari.api.app import create_app
from ari.config import PROJECT_ROOT, Settings
from ari.domain.clinical import CaseReview, ClinicalBundle, VersionRef
from ari.domain.models import new_id
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.yaml_io import parse_bundle
from ari.infrastructure.persistence.sqlite import Base, SqliteSessionRepository

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


def _ensure_schema(engine: Engine, database_url: str) -> None:
    """The single revision is regenerated while no real database exists (docs/MIGRATIONS.md).

    A persistent development database created by an older `0001_initial` reports itself as
    current yet lacks the newer tables; fail early with the remedy instead of at first use.
    """
    missing = set(Base.metadata.tables) - set(inspect(engine).get_table_names())
    if missing:
        raise SystemExit(
            f"Base {database_url} créée par une ancienne révision (tables manquantes : "
            f"{', '.join(sorted(missing))}). Supprimez ce fichier et relancez : la démo le "
            "recrée et republie le contenu synthétique."
        )


def seed_bundles(store: ClinicalStore, directory: Path) -> None:
    for path in sorted(directory.glob("*.yaml")):
        publish_demo_content(store, parse_bundle(path.read_text(encoding="utf-8")))


def serve(
    database_url: str, bundles: Path, provider: Literal["fake", "openai"], port: int
) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    engine = SqliteSessionRepository(database_url).engine
    _ensure_schema(engine, database_url)
    seed_bundles(ClinicalStore(engine), bundles)
    # Live providers read their keys from `.env`; the offline demo stays hermetic.
    settings_class: type[Settings] = Settings if provider == "openai" else DemoSettings
    settings = settings_class(
        environment="development", provider_mode=provider, database_url=database_url
    )
    uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--provider", choices=["fake", "openai"], default="fake")
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="Persistent SQLite file (kept between runs). Default: temporary, removed on exit.",
    )
    parser.add_argument(
        "--bundles",
        type=Path,
        default=DEMO_BUNDLES,
        help="Directory of YAML bundles published with simulated reviews.",
    )
    args = parser.parse_args()
    if args.database is not None:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        serve(f"sqlite:///{args.database.resolve()}", args.bundles, args.provider, args.port)
        return
    with TemporaryDirectory(prefix="ari-mvp-demo-") as directory:
        database_url = f"sqlite:///{Path(directory) / 'demo.db'}"
        serve(database_url, args.bundles, args.provider, args.port)


if __name__ == "__main__":
    main()
