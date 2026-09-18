# Database migrations

Alembic is the only schema mechanism. Application startup never creates or alters tables.
The database is PostgreSQL, and only PostgreSQL: the engine factory refuses any other URL.

```bash
make db-up              # local PostgreSQL via docker compose (roles and databases created once)
make migrate            # upgrade the platform database selected by ARI_DATABASE_URL to head
make migration-check    # verify the database is at head
make db-reset           # drop the docker volume, recreate the server, migrate again
```

`alembic.ini` has one section per database. Today there is `[platform]` (learners, sessions,
the clinical and placement registries), invoked with `-n platform`; the content pipeline adds
`[content]` with its own `env.py`, metadata and revision chain. The default URL is the docker
compose platform database, `postgresql+psycopg://ari_platform:ari_platform@localhost:5432/ari_platform`;
`docker-compose.yml` and `deploy/postgres/init/01-databases.sh` create the `ari_platform` and
`ari_content` roles and databases, each role unable to connect to the other database.

There is a single revision per database, `0001_initial`, which creates the current schema and
then the PL/pgSQL triggers that keep published clinical content, reviews, practice answers and
voice start receipts immutable. The trigger function `ari_immutable()` raises with SQLSTATE
class 23 (`integrity_constraint_violation`) so psycopg surfaces it as `IntegrityError`, exactly
like a violated constraint; the shared helpers live in
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
