## Action 8 : cadre d'évaluation v0 (`ari.evals`)

État au 22 octobre 2026. Branche `claude/ari-evals-fc34de`, à jour de `main` (`fff3bbd`, plan 1.1).
Code et tests terminés, non commités. Mode d'emploi : `docs/EVALS.md`.

### Fait
- **Package `backend/src/ari/evals/`, construit comme le plan le décrit :**
  - `manifest.py` : empreintes SHA-256 des fichiers, source, annotateurs, consentement, partition
    par familles de cas. Le holdout n'est lu qu'en mode gate.
  - `scorers/` : scorers déterministes, aucun juge LLM.
  - `runner.py` : répétitions, une seule nouvelle tentative, coût de chaque appel par
    `costing.cost_of`.
  - `report.py`, `gates.py`, `compare.py` : intervalles par bootstrap apparié, items qui changent
    de verdict, classement par coût (5bis).
  - CLI : `python -m ari.evals run|voice|compare`.
- **Configurations sur le routage de production** (`llm_routing`, PR #16). Chaque route d'une
  configuration correspond à `ARI_<TÂCHE>_PROVIDER` et `ARI_<TÂCHE>_MODEL`. Le fournisseur testé
  est construit par `llm_provider_from_settings` : ce qui est mesuré est ce qui tournerait.
  7 configurations : `fake`, `fake-exam`, `luna`, `luna-exam`, `terra`, routage mixte OpenAI, et
  évaluation sur `claude-sonnet-5-5`. Grille de prix `pricing-v2`. Modèle demandé et modèle
  retourné (`model_returned`, action 5) inscrits par appel.
- **Suites :**
  - `patient-leakage@1` : 80 questions sur 4 cas inventés. Elle tourne sur le patient
    d'entraînement **et** sur celui de la chaîne d'examen (`ExamPatient`, action 3b), comme le plan
    le demande.
  - `evaluation-language@1` : 120 transcripts à erreurs injectées.
  - `evaluation-medical@1` : format et score prêts, données en attente du médecin.
  - `voice-sim@1`, ajout du plan 1.1 : apprenant simulé scripté, cible en processus.
- **`voice-sim@1` en processus** :
  - Sont réels : application, WebSocket, push-to-talk, segmenteur de parole de l'examen,
    orchestrateur, patients, persistance, crédit après lecture, évaluation finale.
  - Sont simulés : STT, LLM et voix. La voix de l'apprenant code le texte dans le signal, et le STT
    simulé le relit exactement.
  - Base PostgreSQL jetable créée puis supprimée. Un compte neuf par session.
  - Mesures : latence fin de parole → premier audio, taux d'erreur par mot de la transcription,
    fuites, inventions, refus, interruption, crédit sans accusé de lecture (vérifié en base), coût
    et durée par session.
- **Seuils** : `evals/gates.yaml` en `gates-v2`, avec les seuils de `voice-sim` (latence examen
  P95 ≤ 4 s, interruption fonctionnelle, aucun crédit sans accusé de lecture).
- **Garde-fous** : contrat import-linter (le code de production n'importe jamais `ari.evals`) ;
  `make evals-fake` exécuté par la CI à chaque commit.
- **Exemples** : `evals/reports/examples/` (rapports `patient-leakage` et `voice-sim`, comparaison
  entraînement contre chaîne d'examen).

### Découvert dans le code (écarts avec ce plan)
1. **Une interruption retarde la réponse suivante de toute la fin de la phrase coupée.** Sur la
   chaîne d'examen, `VoiceSocket._stream_patient_audio` lit la synthèse interrompue jusqu'au bout
   pour enregistrer son coût, et la question suivante attend.
   - Mesure en processus : 817 ms au lieu d'environ 65 ms pour la réponse qui suit l'interruption.
     La voix simulée envoie 100 ms d'audio toutes les 30 ms.
   - Avec ElevenLabs en streaming réel, le retard serait proche de la durée de la phrase restante :
     plusieurs secondes, au-delà de l'objectif P95 de 2,5 s.
   - À traiter dans le code de l'action 3b. Une piste : finir la lecture du flux en tâche de fond,
     en gardant le coût. Non corrigé ici, car hors périmètre.
2. **L'évaluation de session ne peut pas signaler une erreur médicale dans ce que dit
   l'apprenant.** `session-evaluation-v4` n'a aucun champ pour cela. Le seuil « rappel des erreurs
   critiques ≥ 0,90 » n'est atteignable que si le médecin n'annote que des omissions.
3. **`revealed_fact_ids` est le crédit, pas la réponse.** Un tour interrompu ou non confirmé garde
   la réponse du patient dans `selected_fact_ids` et rien dans `revealed_fact_ids`. `voice-sim`
   mesure les fuites sur le premier champ et le crédit sur le second.
4. **Le fournisseur simulé est grossier.** En allemand, il révèle 10 % de faits non demandés. En
   français, il ne reconnaît presque aucune question et répond `out_of_scope`. Les rapports fake
   vérifient la chaîne de mesure, pas la qualité.

### Décisions prises dans mon périmètre
- Datasets hors de l'image Docker (`evals/`). Un item = une famille de cas. Un test vérifie par
  empreinte qu'aucune phrase du holdout n'apparaît dans le dev ni dans un prompt.
- Verdict d'un item = majorité des répétitions. Moyenne de coût `null` dès qu'un appel est sans
  tarif ; une configuration au coût inconnu n'est jamais recommandée.
- Sur la chaîne d'examen, un tour non clinique (politesse, demande de répétition, hésitation)
  compte comme une non-réponse.
- **Apprenant simulé = script, pas un LLM** : mêmes questions à chaque passage, donc des mesures
  comparables, sans coût.
- **Quota d'examens en processus** : un compte neuf par session. Le quota n'est ni touché ni
  contourné.
- `voice-sim` tourne en CI en rapport seulement, sans gate : les seuils de minutage
  n'y ont pas de sens. Les propriétés mécaniques (interruption, crédit) sont vérifiées par les
  tests.
- `voice-sim@1` est écrit en français sur `ARI-DEV-FR`, le seul cas vocal publié par `cases/dev`.

### Points remontés au propriétaire
1. **Cible staging de `voice-sim`.** Ma recommandation pour le quota :
   - un compte d'évaluation dédié, marqué par un administrateur et exempté du quota ;
   - ses sessions marquées « évaluation » et exclues des métriques, du programme et de la page
     Coûts ;
   - le quota inchangé pour tous les autres comptes.

   C'est une migration en production : elle demande votre accord et une revue Opus.
2. Confirmer `invented_fact` comme erreur critique de `patient-leakage`.
3. Lancer la première mesure sur les vrais modèles (`--live`, clés dans le shell) : contrôle a
   posteriori de la bascule sur `gpt-6-luna`, puis `compare`.
4. Brancher le gate dans `promote.yml`, et un workflow manuel sur vrais modèles avec secret.
5. Faire relire les phrases allemandes par un germanophone.
6. Pour le médecin : 30 à 50 transcripts annotés, avec consentement, hors dépôt.
7. Ajouter un champ « erreurs médicales » à l'évaluation : c'est un changement de prompt.
8. Pour l'action 3b : le retard après interruption (découverte 1).

### Mesures
- `patient-leakage@1`, fake, dev, 3 répétitions :
  - patient d'entraînement : `leak_rate` 0,10, `refusal.recall` 0,92 ;
  - chaîne d'examen : mêmes valeurs, aucune formulation libre produite par le fake.
- `evaluation-language@1`, fake : rappel 0 par catégorie ; `code_switch` et `forgotten_section`
  à 1,0.
- `voice-sim@1`, en processus, 1 session par mode :
  - fuites 0, interruption 1,0, crédits sans accusé 0, taux d'erreur par mot 0 ;
  - latence de traitement : P50 79 ms (entraînement) et 65 ms (examen) ; P95 examen 817 ms, à
    cause de la découverte 1 ;
  - coût 0 (fake), 74 appels pour 2 sessions.
- Aucune mesure sur les vrais modèles.

### Reste ouvert
- Points 1 à 8 ci-dessus.
- `voice-sim` : cible staging, moteur `openai_realtime`, scripts en allemand.
- Remplacer la fiabilité 0,5 de l'action 6 par la valeur mesurée (`reliability`).
- **Revue Opus : non requise à mon avis.** Aucun code de production, aucune migration, aucune
  donnée ne change. La CI crée et supprime une base jetable sur son PostgreSQL de test. Le mode
  `--live` lit les clés dans l'environnement, comme l'application.

### Commandes de validation exécutées
- `make lint` (dépendances de `backend/requirements.lock`) : ruff vert, mypy strict sans erreur sur
  194 fichiers, 6 contrats import-linter respectés.
- `make test-backend` sur PostgreSQL 17 local (port 5433). Les 45 tests de `backend/tests/evals/`
  sont verts, et la suite complète aussi : 517 tests verts.
- `make evals-fake` : les quatre exécutions écrivent leur rapport.

## Suite du 24 octobre 2026 : gate de promotion (points 2 et 4)

Branche `claude/project-thread-wk7n4m`, à partir de `main` en `462ed72` (PR #20 fusionnée).

### Fait
- **Point 2 tranché par le propriétaire** : `invented_fact` n'est pas une erreur critique de
  `patient-leakage@1`. Elle compte dans un seuil `invented_fact_rate ≤ 0,02`, aligné sur la fuite de
  faits. `gates.yaml` passe en `gates-v3`. `voice-sim@1` garde `invented_fact` critique : la
  question ne portait pas sur cette suite.
- **Empreinte IA** (`ari/evals/promotion.py`, `python -m ari.evals fingerprint`) : prompts, versions
  chargées par les conteneurs, variables modèle, fournisseur, effort et moteur de
  `docker-compose.yml`, `llm_routing.py`.
- **Verdict de promotion** (`python -m ari.evals promotion`) : les trois runs exigés, sur le holdout,
  en mode gate, sur les vrais modèles.
- **`evals-live.yml`** : workflow manuel, `main` seulement, environnement `evals`, clés en secrets.
- **`promote.yml`** : si l'empreinte du candidat diffère de la dernière mise en production, exige un
  run `evals-live` réussi dont le verdict est `passed` pour cette empreinte ; sinon refuse.
- Mode d'emploi : `docs/EVALS.md`, section 10.

### Décisions prises dans mon périmètre
- L'empreinte suit `docker-compose.yml` et non `config.py` : c'est ce que la production exécute. Un
  test vérifie que chaque réglage IA de `Settings` y figure.
- La comparaison se fait avec la dernière mise en production réussie (artefact gardé 90 jours).
  Une empreinte déjà promue a été vérifiée ; l'absence d'artefact exige le gate (on ne suppose
  jamais).
- Pas de baseline dans les runs gate de promotion : toute erreur critique bloque.
- Aucune dérogation manuelle au gate : à ajouter seulement si le propriétaire le demande.

### Points remontés au propriétaire
1. Créer l'environnement GitHub `evals` et ses secrets `ARI_OPENAI_API_KEY` (et
   `ARI_ANTHROPIC_API_KEY` si besoin).
2. La première mise en production après la fusion exige un run `evals-live` réussi. Les seuils du
   plan n'ont jamais été mesurés sur les vrais modèles : un échec bloquerait la production.
3. Un modèle changé dans le `.env` du serveur échappe au gate (`docs/EVALS.md`, section 9).

### Commandes de validation exécutées
- `make lint` : ruff, mypy strict et import-linter verts.
- `actionlint` (avec shellcheck) sur les workflows : aucun constat.
- `pytest backend/tests` sur PostgreSQL 16 local.
- Aucun appel payant : les clés ne sont pas dans l'environnement de l'agent.
