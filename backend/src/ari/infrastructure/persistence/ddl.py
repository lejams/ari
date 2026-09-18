"""PL/pgSQL building blocks shared by the Alembic revisions of every ARI database.

Append-only guarantees are enforced by the database itself, not by application discipline.
"""

from collections.abc import Sequence
from typing import Protocol


class Operations(Protocol):
    """The slice of ``alembic.op`` these helpers use; keeps them testable and typed."""

    def execute(self, sqltext: str) -> object: ...


# SQLSTATE class 23 is what psycopg maps to IntegrityError; a bare RAISE EXCEPTION would
# surface as DatabaseError and silently bypass every `except IntegrityError` in the code.
IMMUTABLE_FUNCTION = """
CREATE FUNCTION ari_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'integrity_constraint_violation';
END $$
"""
DROP_IMMUTABLE_FUNCTION = "DROP FUNCTION IF EXISTS ari_immutable()"


def changed(columns: Sequence[str]) -> str:
    """A trigger WHEN condition: true when any of the given columns differs (null-safe)."""
    return " OR ".join(f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in columns)


def create_immutable_trigger(
    op: Operations, name: str, event: str, table: str, message: str, when: str | None = None
) -> None:
    condition = f" WHEN ({when})" if when else ""
    op.execute(
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} FOR EACH ROW{condition} "
        f"EXECUTE FUNCTION ari_immutable('{message}')"
    )


def create_append_only_triggers(op: Operations, tables: Sequence[str], message: str) -> None:
    """Refuse every UPDATE and DELETE on the given tables."""
    for table in tables:
        for action in ("UPDATE", "DELETE"):
            create_immutable_trigger(op, f"{table}_no_{action.lower()}", action, table, message)
