# Database migrations

Alembic is the only schema mechanism. Application startup never creates or alters tables.
The database is PostgreSQL, and only PostgreSQL: the engine factory refuses any other URL.

```bash
make db-up              # local PostgreSQL via docker compose (roles and databases created once)
make migrate            # upgrade the platform database selected by ARI_DATABASE_URL to head
make migration-check    # verify the database is at head
make db-reset           # drop the docker volume, recreate the server, migrate again
```

`alembic.ini` has one section per database: `[platform]` (learners, sessions, the clinical and
placement registries; `ARI_DATABASE_URL`, `-n platform`) and `[content]` (the protocol
pipeline; `ARI_CONTENT_DATABASE_URL`, `-n content`, `make migrate-content`). Each has its own
`env.py`, metadata (`Base`, `ContentBase`) and revision chain under `backend/migrations/`.
`docker-compose.yml` and `deploy/postgres/init/01-databases.sh` create the `ari_platform` and
`ari_content` roles and databases, each role unable to connect to the other database.

The first revision of each database (`0001_initial`, `0001_content_initial`) creates the schema
and then the PL/pgSQL triggers (`0002_backoffice_auth` and `0003_bundle_drafts` extend the
content chain the same way): `ari_immutable()` refuses UPDATE/DELETE on
append-only tables and `ari_only_columns_mutable('status', ...)` lets only the listed columns
of a content row change. Both raise with SQLSTATE class 23
(`integrity_constraint_violation`) so psycopg surfaces `IntegrityError`, exactly like a
violated constraint; the shared helpers live in
`backend/src/ari/infrastructure/persistence/ddl.py`. Row locks (`SELECT ... FOR UPDATE`, or
`FOR SHARE` when a session pins a published scenario) serialise workflow mutations.

Tests clone one template database migrated once per session (`backend/tests/conftest.py`,
`ARI_TEST_DATABASE_URL`); `python -m ari.demo` and the tests pass URLs through
`Config.attributes["database_url"]`.

When the models change, delete the revision file and regenerate it while there is no
database to preserve:

```bash
rm backend/migrations/platform/versions/0001_initial.py
ARI_DATABASE_URL=postgresql+psycopg://ari_platform:ari_platform@localhost:5432/ari_platform \
  .venv/bin/alembic -c alembic.ini -n platform revision --autogenerate \
  -m "initial platform schema" --rev-id 0001_initial
```

The target database must be empty (`make db-reset` without the final migrate, or drop and
recreate `ari_platform`). Then rename the generated file to `0001_initial.py`, re-add the
`_create_triggers()` call and block at the end of the revision, run `ruff format` on it, and
run `alembic -n platform check` against a freshly migrated database. Once a real database
exists, add a new revision instead.
