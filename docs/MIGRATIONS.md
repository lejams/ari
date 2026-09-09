# Database migrations

Alembic is the authoritative schema-evolution mechanism. Application startup does not alter an existing database.

## Commands

From the repository root:

```bash
make migrate
make migration-check
```

`ARI_DATABASE_URL` selects the SQLite database. The default is `sqlite:///var/ari.db`.

For a new revision:

```bash
.venv/bin/alembic -c alembic.ini revision -m "describe the change"
```

Review generated migrations before applying them. A migration must support a clean empty database when it is the first revision, preserve existing data, and be exercised by offline tests.

## Historical `e61db6e` upgrade

Revision `20260902_0001` upgrades both an empty database and the historical schema created by `e61db6e`.

Historical sessions have no recoverable voice-transport record. The migration therefore applies the documented compatibility value `pipeline_economy@1` and stores a snapshot with `provider=legacy_unknown` plus a migration marker. This must not be interpreted as reconstructed history.

Historical sessions remain readable. Voice resumption is rejected when that `legacy_unknown` snapshot cannot be matched exactly by the current registry; creating a new session is required instead of silently substituting current models.

Historical turns receive `delivery_status=legacy_unknown` and `audio_delivered_at=NULL`; the migration never claims that old audio was delivered. New application turns explicitly start as `pending`.

Revision `20260903_0002` separates `selected_fact_ids` from `revealed_fact_ids`, adds the persistent response/audio state machine and correlation fields, and records explicit pre-turn voice-stack transitions. Existing `legacy_unknown` history is preserved. Previously pending rows become honestly `delivery_unconfirmed`; their selected facts are retained while unconfirmed facts are removed from scoring.

Provider completion and server-side WebSocket writes are not proof that the client played audio. Pipeline delivery requires a validated full-playback acknowledgement. WebRTC playback completion cannot be proven per response with the browser signals used here, so Realtime turns remain unconfirmed rather than being declared delivered.

Revision `20260903_0003` adds structured execution costs and the versioned voice
telemetry fields. Historical `estimated_cost_usd` values are copied to the structured
amount and explicitly marked `estimated`; missing historical costs remain unknown.
Existing voice metric rows are preserved under schema version
`voice-turn-metric-legacy-v1`. Their old duration values are copied, but no clock
domain is invented and new unavailable fields remain `NULL`.

## Test-only schema creation

`SqliteSessionRepository.initialize_schema()` creates the current schema for isolated temporary tests. It never upgrades an existing schema and must not replace Alembic in development or production.
# Goal 4 — registre clinique

La révision `20260904_0005` ajoute les lots et cas de la zone de brouillons privés
Freiburg, immuables et non exécutables. Elle ne modifie ni cas ni sessions existants.
Voir [FREIBURG_MAPPING.md](FREIBURG_MAPPING.md).

La révision `20260904_0004` ajoute les sources privées référencées, cas, rubriques,
lexiques, scénarios, revues, événements de publication et pins des sessions v2.
Les payloads cliniques sont JSON sous SQLite et JSONB sous PostgreSQL. Les tables
historiques ne sont pas réécrites et aucune référence clinique n'est inventée pour
les anciennes sessions. Les triggers protègent le contenu append-only ; le downgrade
0004 est refusé pour ne pas effacer les preuves/revues. Restaurer une sauvegarde
vérifiée est une opération explicite séparée, pas une migration implicite.

PostgreSQL utilise le pilote optionnel verrouillé :

```sh
.venv/bin/python -m pip install -r backend/requirements-postgres.lock
```

Configurer une URL `postgresql+psycopg://…`, puis utiliser les mêmes commandes
Alembic. Les migrations 0001–0003 livrées restent inchangées. Le lanceur online
active un pont dialectal **uniquement sur sa connexion PostgreSQL** : l'énoncé
exact du backfill `20260903_0003` exige `CAST(CASE … END AS JSON)` pour
`cost_assumptions`. La reconnaissance couvre l'énoncé complet sans paramètres ;
aucune requête similaire arbitraire n'est réécrite. Le hook est retiré après la
migration, ne touche ni SQLite ni le runtime, et n'installe aucun cast global.

Test PostgreSQL réel, sur une base jetable locale explicitement nommée
`ari_goal4_test*` (jamais une base de production) :

```sh
ARI_TEST_POSTGRES_URL='postgresql+psycopg://USER@localhost/ari_goal4_test' \
  .venv/bin/pytest backend/tests/test_clinical_postgres.py
```

Le test crée un schéma isolé aléatoire, exécute les migrations historiques,
vérifie les deux branches de backfill, upgrade 0004, teste JSONB, clés étrangères,
immutabilité et publication concurrente, puis supprime seulement son schéma.
La CI fournit PostgreSQL 17 dans un service isolé. Sans URL explicite, ce test
est signalé *skipped*, jamais compté comme un succès PostgreSQL.

## Goal 5 — profil et exercices

- `20260904_0006` : `profile_credentials`, secret stocké uniquement par SHA-256,
  expiration serveur. Aucun rattachement automatique aux profils historiques.
- `20260904_0007` : `practice_runs` et `practice_answers` en JSON/JSONB. Snapshot
  immuable, démarrage/réponses idempotents, pause/reprise, feedback figé et vérifié
  contre les réponses et la spécification exacte. Triggers SQLite/PostgreSQL.
- `20260904_0008` : `voice_learning_context` et `voice_start_requests`, mode explicite
  Training/Exam et reçu de démarrage atomique. Les anciennes sessions ont un mode
  inconnu (`null`), sans backfill fictif. Les deux tables sont immuables.

Ces migrations sont additives ; aucune base applicative existante n’a été migrée
pendant le Goal 5. Le lanceur `python -m ari.demo` utilise une base temporaire neuve.
Les tests PostgreSQL utilisent exclusivement les schémas aléatoires jetables du
cluster de test, jamais la base privée Freiburg.

```sh
ARI_TEST_POSTGRES_URL='postgresql+psycopg://USER@localhost/ari_goal4_test' \
  .venv/bin/pytest backend/tests/test_clinical_postgres.py backend/tests/test_practice_postgres.py
```
