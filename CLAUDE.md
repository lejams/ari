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
- Les autres actions (2, 4, 8, 9, 10, 13) peuvent être faites sur Sonnet 5.5 sans revue Opus systématique,
  sauf si un changement touche à la sécurité, aux données ou à l'état (voir `AGENTS.md`, section Review).

## Avancement

Mettre à jour ce tableau à la fin de chaque étape. **Pour reprendre le travail dans une nouvelle
session, lire d'abord `docs/HANDOFF.md`.** État au 21 octobre 2026, `main` en `b1458b1`.

| Étape | Branche | État | Note |
|---|---|---|---|
| Phase 0 + Action 1 Coûts | `feature/cost-accounting` | **fusionnées** (PR #9) | `docs/CAPABILITIES.md`, `docs/DEPENDENCIES.md`, grille de prix et coût par appel |
| Action 2 Limites structurelles | `feature/usage-limits` | **fusionnée** (PR #10) | une session vocale à la fois, quota de 2 examens, durée de l'examen dérivée du scénario, inactivité, coupe-circuit d'abus optionnel |
| Action 4 Cycle de vie du contenu | `feature/content-lifecycle` | **fusionnée** (PR #11) | item 1 (remplacement des révisions) et item 2 (scan des détails identifiants dans le texte généré) gardés ; item 3 retiré à la demande du propriétaire |
| Action 3 Voix ElevenLabs | 3a : `spike/elevenlabs-tts` (jetable, ne pas fusionner). 3b item 1 : `feature/elevenlabs-tts` (PR #15). 3b items 2 à 6 : `claude/project-thread-w4yibc` | **3a : GO du propriétaire le 20 octobre 2026, sans mesure** (`docs/spikes/exam-elevenlabs.md`, section 8). **3b item 1 fusionné** (adaptateur, PR #15). **3b items 2 à 6 en PR** : moteur `application/services/exam_session.py` + `api/exam_socket.py`, désactivé par défaut (`ARI_EXAM_ENGINE=openai_realtime`) ; persona par voix, réglages et balises (`ARI_ELEVENLABS_EXAM_PERSONAS`), voix par défaut `3nMIMZ7RlGwsq1WLgxY3` choisie le 22 octobre 2026 | note : `docs/reviews/action3b-note-de-fin.md`. Avant bascule en production : rapport `patient-leakage@1` (action 8) sur `patient-exam-v1`, essai avec la vraie clé, réglage des seuils de détection de parole. Échéance dure : `gpt-4o-mini-tts` s'arrête le 6 janvier 2027 |
| Test A/B de la voix d'entraînement | `claude/project-thread-obd8he` | **en PR** (décision du propriétaire du 31 octobre 2026 : « oui go ») | `ARI_TTS_PROVIDER=ab` : chaque session d'entraînement tire OpenAI (`gpt-4o-mini-tts`) ou ElevenLabs (`eleven_v4_turbo`) 50/50 et le garde (épinglé dans `voice_stack_config`) ; repli explicite sur OpenAI sans clé ou si ElevenLabs échoue avant le premier son ; compte forcé par la CLI `voice-provider` (migration `0006_learner_tts_provider`) ; secret `ARI_ELEVENLABS_API_KEY` ajouté à la synchro (promotion et rotation) ; tableau « Voix de l'entraînement » sur la page Coûts. Examen inchangé. Étapes d'Ismail : `docs/DEPLOYMENT.md`. Prix ElevenLabs de `pricing-v2` à recaler sur les crédits réels (forfait Creator), décision du propriétaire |
| Action 6 Noyau `ari.learning` | `feature/learning-core` | **fusionnée** (PR #13), après revue Opus et relecture de ses correctifs | `docs/LEARNING_CORE.md`, brief : `docs/reviews/action6-opus-review-brief.md` |
| Action 5 Évaluations immuables | `claude/evaluation-runs-51a8b7` | **fusionnée** (PR #14), après revue Opus | `evaluation_runs` en ajout seul (migration `0004_evaluation_runs`), `evaluations` devient un pointeur. Brief : `docs/reviews/action5-opus-review-brief.md`, note : `docs/reviews/action5-note-de-fin.md` |
| Action 7 Taxonomie FSP et observations | | à faire (revue Opus obligatoire) | **bloquée par un atelier de 4 h avec le médecin** (taxonomie, erreur critique) |
| Action 8 Cadre d'évaluation `ari.evals` | `claude/ari-evals-fc34de` (v0) ; compte d'évaluation : `claude/project-thread-f6hwc2` ; gate : `claude/project-thread-wk7n4m` | **v0 fusionnée** (PR #20). **Compte d'évaluation fusionné** (PR #23, migration `0005_evaluation_accounts`, revue Codex seule sur décision du propriétaire). **Gate de promotion en PR** (#24) | `docs/EVALS.md`, note `docs/reviews/action8-note-de-fin.md`. Suites `patient-leakage@1` (entraînement et chaîne d'examen), `evaluation-language@1`, `voice-sim@1` en processus (synthétiques) ; `evaluation-medical@1` **en attente des annotations du médecin**. Compte d'évaluation (choix A du 24 octobre 2026) : marqué en ligne de commande, sans quota d'examens, sessions hors métriques, programme et page Coûts. `invented_fact` non critique (propriétaire, 24 octobre 2026, `gates-v3`). Gate : empreinte IA, `evals-live.yml` (environnement `evals`, secrets à créer), `promote.yml` exige un verdict quand l'empreinte change. Restent : cible staging de `voice-sim` (staging force les fakes). **Première mesure sur les vrais modèles (29 octobre 2026) : `evaluation-language@1` échoue** (précision `word_choice` 0, `word_order` 0,82, hallucination de preuve 0,035, instabilité 0,23). Correctif en PR (branche `claude/project-thread-05v093`, aucune dérogation aux seuils) : jeu `evaluation-language@2`, scorer v2, prompt `evaluation-v8`, garde déterministe, `gates-v4`, workflow `evals-compare.yml` (PR #41, #42 fusionnées). Mesure luna sur le dev (31 octobre 2026) : précision et hallucination passent, rappel `word_choice` 0,58 et instabilité 0,18 échouent (le modèle sous-déclare) ; prompt `evaluation-v9` fusionné (PR #43) : tout passe sur le dev sauf l'instabilité, pile à 0,10 ; marge en PR (`evaluation-v10`, règle de code sur le préverbe coupé), puis holdout une seule fois. **Changement de prompt de production** (plan, section 6, point 7) ; le holdout neuf n'est lancé qu'une fois |
| Actions 9, 10, 11, 12 | | à faire | 10 exige une rubrique Arztbrief validée ; 11 et 12 exigent une revue Opus ; 12 exige les textes juridiques |
| Action 13 Tests et audit UI/UX | rapport : `claude/project-thread-8eq3x0` ; chantiers A, C, D regroupés : `claude/project-thread-kskzck` | **Rapport fusionné** (PR #27). **Chantier B fusionné** (PR #29 : M4, M8, M9, m1, m2 ; axe-core 4.10.3 et contrôle de débordement à 1280 et 390 px dans `practice-e2e.mjs`). **Chantiers A, C, D en une seule PR** (règle du 25 octobre 2026 : une fonctionnalité, une PR ; remplace les PR #30, #31, #32) : A accueil et programme (M1, M2, M3, m10, `program-rules-v3`, « Faire le test plus tard »), C messages et états d'erreur (M5, M6, M10, m5, m8), D textes (M7, M11, m3, m4, m6, m9, libellés « Entraînement / Examen »), fusionnés (PR #34). **Chantier E en PR** (`claude/project-thread-gdtff7`) : direction A sur connexion, application et page vocale, Atkinson Hyperlegible Next auto-hébergée (`web/fonts/`, plus d'appel à Google Fonts sur ces pages), palette et typographie A, navigation en liens, accueil qui cite la première phrase du patient avant un entraînement (`opening_statement` dans `/api/cases`), retour qui commence par « À travailler pour la prochaine fois » | rapport `docs/reviews/ux-app-audit.md` (skills `ux-audit` et `design-audit` introuvables : grille du plan appliquée à la main). Direction visuelle A (Continuité) choisie par le propriétaire le 24 octobre 2026 parmi trois maquettes ; M3 tranché le 24 octobre 2026 (ordre du parcours gardé, bouton « plus tard »). Restent : extension de l'e2e aux autres parcours (étape 4), test avec 3 à 5 médecins (Ismail), passe landing avant J4 (`cro-optimization`), où `landing.html` charge encore Google Fonts |

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
