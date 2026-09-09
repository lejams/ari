"""Dialect bridge for one frozen Goal 3 backfill; no server-global casts.

SQLite accepts the CASE result as JSON; PostgreSQL requires an explicit cast.
Keep delivered migration files byte-for-byte unchanged. Match the entire normalized
historical statement, not arbitrary SQL, and attach only to the Alembic connection.
"""

LEGACY_BACKFILL = """
UPDATE executions
SET cost_status = CASE
    WHEN estimated_cost_usd IS NULL THEN 'unknown'
    ELSE 'estimated'
END,
cost_amount_usd = estimated_cost_usd,
cost_assumptions = CASE
    WHEN estimated_cost_usd IS NULL THEN '[]'
    ELSE '["legacy_estimated_cost_without_structured_units"]'
END,
cost_unknown_reason = CASE
    WHEN estimated_cost_usd IS NULL
    THEN 'Historical execution has no structured cost'
    ELSE NULL
END
"""


def adapt_legacy_backfill(connection, cursor, statement, parameters, context, executemany):
    if not parameters and not executemany and (
        " ".join(statement.split()) == " ".join(LEGACY_BACKFILL.split())
    ):
        statement = LEGACY_BACKFILL.replace(
            "cost_assumptions = CASE", "cost_assumptions = CAST(CASE"
        ).replace("END,\ncost_unknown_reason", "END AS JSON),\ncost_unknown_reason")
    return statement, parameters
