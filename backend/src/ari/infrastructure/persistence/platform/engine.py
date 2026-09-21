"""PostgreSQL engine for the platform database. No other dialect is supported."""

from sqlalchemy import Engine, create_engine

POSTGRES_SCHEME = "postgresql+psycopg://"


def create_platform_engine(database_url: str) -> Engine:
    if not database_url.startswith(POSTGRES_SCHEME):
        raise ValueError(
            f"ARI n'accepte que PostgreSQL via psycopg ({POSTGRES_SCHEME}...) ; reçu : "
            f"{database_url.split('://', 1)[0]}://"
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        # A naive datetime written into a timestamptz column is interpreted in the session
        # time zone; pinning UTC keeps the stored instant equal to the application's UTC.
        connect_args={"options": "-c timezone=UTC"},
    )
