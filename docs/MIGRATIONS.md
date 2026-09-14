# Database migrations

Alembic is the only schema mechanism. Application startup never creates or alters tables.

```bash
make migrate            # upgrade the database selected by ARI_DATABASE_URL to head
make migration-check    # verify the database is at head
```

The default database is `sqlite:///var/ari.db`. There is a single revision,
`0001_initial`, which creates the current schema and the SQLite triggers that keep
published clinical content, practice answers and voice start receipts immutable.

Tests and `python -m ari.demo` run the same revision against a temporary database
by passing the URL through `Config.attributes["database_url"]`.

When the models change, delete the revision file and regenerate it while there is no
database to preserve:

```bash
rm backend/migrations/versions/0001_initial.py
ARI_DATABASE_URL=sqlite:////tmp/ari-empty.db .venv/bin/alembic -c alembic.ini revision \
  --autogenerate -m "initial schema" --rev-id 0001_initial
```

Then re-add the trigger block at the end of `upgrade()` and run `make migration-check`
against a freshly migrated database. Once a real database exists, add a new revision
instead.
