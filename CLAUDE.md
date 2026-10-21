# CLAUDE.md

@AGENTS.md

## Instruction de travail

Le plan d'exécution de référence est `ARI_plan_claude_code.md` (racine du dépôt).
Avant toute tâche : relire la section du plan concernée, ses invariants (section 2), ses constantes
(section 3) et les points réservés au propriétaire (section 6 ; le texte du plan les appelle par erreur
« section 12 »).

Règles condensées :

- Avancer **étape par étape**, dans l'ordre de dépendance du plan. Une action = une branche, une PR,
  des tests verts, une note de fin (modèle en section 7 du plan).
- Ne jamais commencer une action tant que ses dépendances ne sont pas fusionnées sur `main`.
- S'arrêter et demander dès qu'une décision relève de la section 6 (tarifs, voix ElevenLabs,
  taxonomie, rubriques, passage spike 3a → 3b, paiement et textes juridiques, retrait d'un
  comportement visible, changement de prompt ou de modèle en production, format FSP par Land).
- Aucune abstraction sans usage FSP immédiat. « Le LLM propose, le code décide. »
- Commit et push uniquement à la demande de l'utilisateur. `gh` n'est pas installé : les PR sont
  ouvertes par l'utilisateur.

## Revue Opus obligatoire par PR

Les actions **5, 6, 7, 11 et 12** exigent une **revue par Opus 5.5 avant fusion**, une revue par PR
(pas une seule revue à la fin). Raison : une erreur silencieuse y est coûteuse ou difficile à corriger
ensuite (migration sur une table en ajout seul, calculs de maîtrise, adaptateur d'observations et ses
invariants 3 et 4, versionnement du gold et propagation, paiement et effacement RGPD).

- Ne pas déclarer l'action terminée tant que la revue n'a pas eu lieu et que ses constats ne sont pas
  traités. Noter dans la note de fin : qui a relu, quelle PR, ce qui a été corrigé.
- L'action 3 n'est pas dans la liste mais le passage du spike 3a à 3b reste une décision du
  propriétaire.
- Une revue de fin sur l'ensemble s'ajoute à ces revues, elle ne les remplace pas.
- Les autres actions (2, 4, 8, 9, 10) peuvent être faites sur Sonnet 5.5 sans revue Opus systématique,
  sauf si un changement touche à la sécurité, aux données ou à l'état (voir `AGENTS.md`, section Review).

## Avancement

Mettre à jour ce tableau à la fin de chaque étape. **Pour reprendre le travail dans une nouvelle
session, lire d'abord `docs/HANDOFF.md`.** État au 21 octobre 2026, `main` en `b1458b1`.

| Étape | Branche | État | Note |
|---|---|---|---|
| Phase 0 + Action 1 Coûts | `feature/cost-accounting` | **fusionnées** (PR #9) | `docs/CAPABILITIES.md`, `docs/DEPENDENCIES.md`, grille de prix et coût par appel |
| Action 2 Limites structurelles | `feature/usage-limits` | **fusionnée** (PR #10) | une session vocale à la fois, quota de 2 examens, durée de l'examen dérivée du scénario, inactivité, coupe-circuit d'abus optionnel |
| Action 4 Cycle de vie du contenu | `feature/content-lifecycle` | **fusionnée** (PR #11) | item 1 (remplacement des révisions) et item 2 (scan des détails identifiants dans le texte généré) gardés ; item 3 retiré à la demande du propriétaire |
| Action 3 Voix ElevenLabs | 3a : `spike/elevenlabs-tts` (jetable, ne pas fusionner). 3b : `feature/elevenlabs-tts` | **3a : GO du propriétaire le 20 octobre 2026, sans mesure** (`docs/spikes/exam-elevenlabs.md`, section 8). **3b item 1 fait** : adaptateur `infrastructure/providers/elevenlabs/tts.py` (WebSocket Text to Dialogue, pool de connexions, coût `tts_character`), désactivé par défaut (`ARI_TTS_PROVIDER=openai`) | protocole non confronté au service réel : premier appel avec la clé = essai. Reste : choix des `voice_id` par le propriétaire, items 2 à 6 de 3b (moteur d'examen en chaîne). Échéance dure : `gpt-4o-mini-tts` s'arrête le 6 janvier 2027 |
| Action 6 Noyau `ari.learning` | `feature/learning-core` | **fusionnée** (PR #13), après revue Opus et relecture de ses correctifs | `docs/LEARNING_CORE.md`, brief : `docs/reviews/action6-opus-review-brief.md` |
| Action 5 Évaluations immuables | `claude/evaluation-runs-51a8b7` (non commitée) | **code terminé, revue Opus faite, constats traités puis contre-vérifiés (verdict final : approuvé), 403 tests verts, `alembic check` propre sur les deux chaînes. À commiter, pousser et fusionner** | `evaluation_runs` en ajout seul (migration `0004_evaluation_runs`), `evaluations` devient un pointeur. Brief : `docs/reviews/action5-opus-review-brief.md`, note : `docs/reviews/action5-note-de-fin.md` |
| Action 7 Taxonomie FSP et observations | | à faire (revue Opus obligatoire) | **bloquée par un atelier de 4 h avec le médecin** (taxonomie, erreur critique) |
| Actions 8, 9, 10, 11, 12 | | à faire | 10 exige une rubrique Arztbrief validée ; 11 et 12 exigent une revue Opus ; 12 exige les textes juridiques |

Décisions du propriétaire (15 octobre 2026) :
- tous les modèles texte alignés sur `gpt-6-luna` (remplace la décision intermédiaire « terra seul »),
  appliqué aux défauts de `config.py`, `docker-compose.yml` et `deploy/env.example` ; évaluation et
  contenu inclus, sans mesure préalable (contrôle a posteriori par `ari.evals`, retour sur
  `gpt-5.6-terra` par variable d'environnement) ;
- 18 octobre 2026 : **pas de budget IA mensuel fixé a priori** (le « 6 € » du plan est supprimé). Principe :
  minimiser le coût sous contrainte de qualité optimale, jamais par coupure. Limites retenues :
  structurelles (une session vocale à la fois, durée dérivée du scénario, inactivité, quota de 2 examens
  de l'offre) et un coupe-circuit d'abus optionnel, désactivé par défaut. Le coût baisse par routage,
  cache et pré-génération, avec choix de la configuration la moins chère parmi celles qui passent les
  seuils d'`ari.evals` (action 8, point 5bis). Ne jamais introduire de plafond en euros sans accord ;
- la production utilise `gpt-realtime-2.1-mini`, non concerné par le retrait de janvier 2027 ;
- la TTS ElevenLabs sera testée sur l'entraînement et basculée avant le retrait de `gpt-4o-mini-tts`
  le 6 janvier 2027 ; le spike 3a couvre donc l'entraînement et l'examen. Réordonnancement proposé
  (spike sur l'entraînement d'abord), pas encore tranché.

## Commandes réelles (Makefile)

- Base locale : `make db-up` (PostgreSQL en Docker, requis par tous les tests backend).
- Tests : `make test-backend` (lancer pytest depuis la racine, les chemins d'`alembic.ini` en dépendent),
  `make test-web`, `make test-backoffice-web`.
- Qualité : `make lint` (ruff, mypy strict, import-linter).
- Migrations : `make migrate`, `make migrate-content`, puis
  `.venv/bin/alembic -c alembic.ini -n platform check` et `-n content check`.

## Pièges connus

- Docker n'est pas accessible depuis WSL sur ce poste. PostgreSQL 16 de secours, sans droits
  administrateur, via le paquet Python `pgserver` : créer un venv hors du dépôt, `pip install
  pgserver==0.1.4`, puis avec les binaires de `site-packages/pgserver/pginstall/bin` :
  `initdb -D <dir> -U postgres --pwfile=<fichier contenant "postgres"> -A scram-sha-256 -E UTF8
  --locale=C.UTF-8`, puis `pg_ctl -D <dir> -o "-p 5432 -c listen_addresses=127.0.0.1" start`. Les tests
  utilisent `postgres:postgres@localhost:5432` et créent leurs bases eux-mêmes. Pour `make migrate`,
  créer en plus les rôles et bases `ari_platform` et `ari_content` (mot de passe identique au nom).
  Référence au 19 octobre 2026 : 356 tests backend verts.

- Les outils ne peuvent ni lire ni écrire les fichiers `.env*` ni `.git/`.
- `ruff format` uniquement sur les fichiers modifiés (`ruff format backend` reformate des fichiers non
  touchés).
- Les modèles cliniques stricts refusent les listes et les chaînes pour les enums en mode Python :
  reconstruire depuis du JSON avec `model_validate_json(json.dumps(...))`.
- Les commandes shell composées (`&&`, boucles) sont souvent refusées : préférer des commandes simples.
