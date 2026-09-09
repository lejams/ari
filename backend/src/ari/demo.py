"""Run the offline MVP against a fresh temporary database, never an existing one."""

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from alembic import command
from alembic.config import Config
from pydantic_settings import SettingsConfigDict

from ari.api.app import create_app
from ari.config import PROJECT_ROOT, Settings


class DemoSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="ARI_", extra="ignore")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ari-mvp-demo-") as directory:
        database_url = f"sqlite:///{Path(directory) / 'demo.db'}"
        previous_url = os.environ.get("ARI_DATABASE_URL")
        os.environ["ARI_DATABASE_URL"] = database_url
        try:
            command.upgrade(Config(PROJECT_ROOT / "alembic.ini"), "head")
        finally:
            if previous_url is None:
                os.environ.pop("ARI_DATABASE_URL", None)
            else:
                os.environ["ARI_DATABASE_URL"] = previous_url
        settings = DemoSettings(
            environment="development",
            provider_mode="fake",
            database_url=database_url,
            enable_mvp_demos=True,
            auto_create_schema=False,
            enable_english_technical_test=False,
        )
        uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
