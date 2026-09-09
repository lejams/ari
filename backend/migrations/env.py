from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util import load_python_file
from sqlalchemy import engine_from_config, event, pool

from ari.config import Settings
from ari.infrastructure.persistence.sqlite import Base, _enable_sqlite_foreign_keys

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = Settings().database_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(connectable, "connect", _enable_sqlite_foreign_keys)
    with connectable.connect() as connection:
        bridge = None
        if connection.dialect.name == "postgresql":
            bridge = load_python_file(Path(__file__).parent, "legacy_postgres.py")
            event.listen(
                connection, "before_cursor_execute", bridge.adapt_legacy_backfill, retval=True
            )
        try:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if bridge is not None:
                event.remove(connection, "before_cursor_execute", bridge.adapt_legacy_backfill)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
