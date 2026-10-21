## Action 5 : évaluations immuables

État au 21 octobre 2026 : code, migration et tests terminés. La revue Opus obligatoire a eu lieu
(verdict : approuvé avec réserves) et tous ses constats sont traités (voir la fin de la note). Le
travail n'est pas encore commité ; la PR reste à ouvrir.

Fait :
- Table `evaluation_runs` en ajout seul (`backend/migrations/platform/versions/0004_evaluation_runs.py`) :
  `id`, `session_id`, `run_no`, `payload` JSONB, `schema_version`, `prompt_version`, `prompt_hash`,
  `model`, `model_returned`, `rubric_version`, `execution_id`, `created_at`. `UPDATE` et `DELETE`
  refusés par les triggers générés par `ddl.create_append_only_triggers`, comme les autres tables
  de preuves. `run_no` est unique par session et commence à 1.
- `evaluations` devient le pointeur vers le run courant (`current_run_id`, non nul). Une clé
  étrangère composée `(session_id, current_run_id)` garantit que le run pointé appartient à la même
  session. La colonne `payload` est conservée et réécrite à l'identique du run courant, pour les
  lecteurs de l'ancienne table.
- `save_analysis` (`infrastructure/persistence/platform/repository.py`) insère un nouveau run puis
  déplace le pointeur, dans une seule transaction, sous un verrou `FOR NO KEY UPDATE` de la ligne
  de session : deux analyses concurrentes reçoivent des numéros distincts. Elle reçoit désormais
  l'exécution qui a produit l'évaluation et relit sa ligne enregistrée. Elle refuse une exécution
  jamais enregistrée, en échec, d'une autre session, d'une autre opération que `session_evaluation`
  ou d'une autre version de prompt. La provenance du run (`prompt_hash`, `model`, `model_returned`)
  est copiée depuis cette ligne. `created_at` du run est celui de l'évaluation, comme pour les runs
  issus du backfill.
- `get_session` lit le payload du run courant. L'API `/api/sessions/{id}` est inchangée (test).
- Backfill : chaque évaluation existante devient le run 1 de sa session, d'identifiant
  `backfill:<session_id>`. Il est rejouable sans doublon (`NOT EXISTS`, pointeur déjà posé gardé),
  testé deux fois et à travers un aller-retour `downgrade` / `upgrade`.
- `ExecutionRecord.model_returned` et la colonne `executions.model_returned` : le modèle que le
  fournisseur dit avoir utilisé (`response.model` pour OpenAI), distinct du modèle demandé.
- Tests : `backend/tests/test_evaluation_runs.py` (10 tests) et une assertion ajoutée dans
  `test_voice_stacks.py`.
- Doc : paragraphe sur 0004 dans `docs/MIGRATIONS.md`, retour arrière dans `docs/DEPLOYMENT.md`
  (section 10).

Découvert dans le code (écarts avec ce plan) :
- La « ré-analyse » n'a lieu qu'après un échec : `end_session` rend la session telle quelle si elle
  est `completed`, y compris par la route `/analysis/retry`. Une deuxième analyse ne se produit donc
  que si l'analyse précédente a été enregistrée puis que la suite a échoué (par exemple l'ingestion
  du carnet), ou si le statut a été remis à `analysis_failed`. Comportement conservé.
- Le modèle retourné par le fournisseur n'était enregistré nulle part. Le plan demande la colonne
  `model_returned` : il a fallu l'ajouter à `ExecutionRecord`.
- Le double de test OpenAI de `test_voice_stacks.py` n'avait pas d'attribut `model`, que l'API réelle
  fournit toujours : complété.
- Les observations de vocabulaire restent supprimées puis réinsérées à chaque analyse, comme le plan
  le demande ici. À traiter à l'action 7.
- `session_metrics` reste écrasée à chaque analyse. Ce n'est pas une table de preuves : ses valeurs
  dérivent des critères du payload, conservé dans chaque run.

Décisions prises dans mon périmètre :
- Une analyse en échec technique (timeout, refus, garde de sortie structurée) ne crée **aucun** run et
  ne déplace pas le pointeur. L'échec reste tracé dans `executions` seulement (invariant 4) :
  l'action 7 ne pourra jamais en tirer une observation.
- Backfill : un run n'est relié à son exécution que si la session a exactement une exécution
  `session_evaluation` réussie, de même version de prompt. Sinon `execution_id`, `model` et
  `prompt_hash` restent `NULL` : inconnu visible plutôt que deviné (esprit de l'invariant 9).
  `model_returned` est `NULL` pour tout l'historique.
- `current_run_id` non nul : toute évaluation a un run. Conséquence : l'ancien code applicatif ne
  peut plus créer d'évaluation sur le nouveau schéma. Un retour arrière de l'application seule
  exige donc d'abord `alembic downgrade 0003_execution_costs`.
- Le `downgrade` garde l'analyse courante de chaque session et supprime les runs antérieurs. Il perd
  donc des preuves ; c'est écrit dans la migration et dans `docs/MIGRATIONS.md`.
- Pas de trigger sur `TRUNCATE` ni sur le pointeur (recul ou suppression), comme pour les autres
  tables en ajout seul du dépôt. Aucune méthode de lecture des runs n'est ajoutée au port : rien ne
  l'utilise encore, l'action 7 l'ajoutera pour son backfill.

Points remontés au propriétaire :
- Aucun point de la section 6 n'a été touché : pas de prompt, pas de modèle, pas de comportement
  visible retiré.
- Le retour arrière de l'application au-delà de 0004 passe uniquement par la restauration du jeu
  `predeploy-<sha>` : l'ancien code est incompatible avec le schéma. C'est écrit dans
  `docs/DEPLOYMENT.md`, section 10, sur recommandation de la revue.
- Nom de branche : celle du worktree, `claude/evaluation-runs-51a8b7`. Le plan suggère plutôt
  `feature/evaluation-runs`. À renommer avant de pousser si vous le souhaitez.

Mesures :
- 403 tests backend verts, dont 10 nouveaux. La référence sur `main` était de 393.
- Onze mutations, toutes détectées par les tests. Avant la revue : retrait du verrou, du
  `NOT EXISTS`, de la règle « exactement une exécution », du trigger, de la garde sur le statut, de la
  comparaison du prompt. Après la revue : `commit` au lieu de `flush` (atomicité), `max(run_no)`
  global, retrait de la garde `current_run_id IS NULL` du backfill, statut lu sur l'objet passé au
  lieu de la ligne enregistrée, retrait du contrôle d'opération.
- Pas de coût ni de latence concernés.

Reste ouvert :
- Commit, push et PR (à la demande du propriétaire).
- Action 8 : lire `ExecutionRecord.model_returned` au lieu de `model_returned_captured=False`
  (`runner.py`, `report.py`). Signalé dans le dossier de notes partagé.
- Hors périmètre, relevé par la revue : `backend/tests/conftest.py` supprime au démarrage toutes les
  bases `ari_test_template%` (`WITH (FORCE)`). Deux suites lancées en même temps sur un même serveur
  se détruisent l'une l'autre.
- Action 7 : backfill des observations depuis `evaluation_runs`. Clé naturelle
  `attempt_ref + producer + skill_id + evidence_key`, avec `attempt_ref = session:<id>`. Les runs
  successifs d'une même session sont des réévaluations : le noyau `ari.learning` les remplace.
- Les observations de vocabulaire (action 7).
- Protection contre `TRUNCATE ... CASCADE`, pour toutes les tables en ajout seul d'un coup (tâche à
  part, la revue partage ce choix). Pas de garde sur le recul du pointeur : ce serait spéculatif.

Commandes de validation exécutées :
- `make lint` : ruff, mypy strict sur `backend/src` et import-linter, 5 contrats respectés. Exécuté
  avec les versions de `backend/requirements.lock` ; avec SQLAlchemy 2.1, mypy signale 13 erreurs
  dans des fichiers non modifiés.
- `make test-backend` depuis la racine : 403 tests verts.
- `.venv/bin/alembic -c alembic.ini -n platform check` et `-n content check` sur des bases fraîches
  migrées : « No new upgrade operations detected ». Aussi `downgrade 0003_execution_costs` puis
  `upgrade head`, à vide et avec données (tests).
- Environnement : PostgreSQL 17 Homebrew sur le port 5432, lancé dans l'espace temporaire de la
  session. Sur macOS, `pg_ctl` exige `LC_ALL=C.UTF-8`, sinon le serveur refuse de démarrer
  (« postmaster became multithreaded »). Python 3.14 (le projet cible 3.12).

Revue Opus :
- Relecteur : Claude Opus 5.5, session de revue séparée, en lecture seule, le 21 octobre 2026, sur
  le travail non commité du worktree `claude/evaluation-runs-51a8b7` (pas encore de PR). Verdict :
  **APPROUVÉ AVEC RÉSERVES**, aucun constat bloquant, 4 mineurs et 6 remarques. Rapport :
  `revue-action5-opus.md`, dans le dossier de notes partagé des sessions.
- MINEUR 1, atomicité non testée : test ajouté (échec au commit après l'écriture du run → ni run ni
  pointeur).
- MINEUR 2, numérotation par session non testée : assertion ajoutée (le premier run d'une deuxième
  session est le run 1).
- MINEUR 3, gardes sur l'objet en mémoire : `save_analysis` relit la ligne `executions` et la
  contrôle, opération comprise ; la provenance du run est copiée depuis cette ligne. Test : une
  exécution enregistrée en échec, passée comme réussie, est refusée.
- MINEUR 4, retour arrière : ligne ajoutée dans `docs/DEPLOYMENT.md`, section 10.
- REMARQUE 5, rejeu du backfill après un run 2 : testé, le pointeur reste sur le run 2.
- REMARQUE 6, pointeur et `TRUNCATE` : laissés tels quels, choix partagé par la revue.
- REMARQUE 7 : verrou passé à `FOR NO KEY UPDATE`.
- REMARQUE 8 : `created_at` d'un nouveau run = `evaluation.created_at`.
- REMARQUE 9 : docstring de la migration et `docs/MIGRATIONS.md` signalent la perte de
  `executions.model_returned` au `downgrade` ; `CLAUDE.md` corrigé (action 6 fusionnée, `main` en
  `b1458b1`).
- REMARQUE 10 : note laissée pour la session de l'action 8.
- Contre-vérification des correctifs par le relecteur (21 octobre 2026) : `make lint` vert, 403 tests
  verts, `alembic check` propre sur les deux chaînes. Les 3 mutations qui survivaient sont maintenant
  tuées, de même que 4 nouvelles (garde de statut en base, garde d'opération, verrou retiré, date du
  run). Pour la dernière, le relecteur a ajouté une assertion : un nouveau run porte la date de son
  évaluation. Plus aucune réserve.
