"""PostgreSQL engine for the content database; same rules as the platform engine."""

from sqlalchemy import Engine

from ari.infrastructure.persistence.platform.engine import create_platform_engine


def create_content_engine(database_url: str) -> Engine:
    return create_platform_engine(database_url)
