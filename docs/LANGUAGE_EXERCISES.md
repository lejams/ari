# Banque d'exercices de langue

Partie hors ligne de la PR 3 du plan `docs/PLAN_NIVEAU_ET_REMEDIATION.md` (section 5). Cette PR
construit la banque, ses contrôles et leur mesure. **Elle ne sert encore aucun apprenant** : le
tirage, le niveau de travail par catégorie, le bouton « Signaler une erreur » et le créneau du
programme viennent après la fusion de la PR 1 (remédiation) et de la PR 2 (test de niveau).

Direction produit (Ismail, 2 novembre 2026) : les apprenants veulent progresser en allemand comme
langue. Un exercice entraîne la grammaire, le vocabulaire ou le registre, à B1, B2 ou C1. La
médecine n'est qu'un décor : personne n'est interrogé sur un savoir médical, et un exercice n'est
jamais un extrait d'un protocole gold.

## 1. L'exercice

Code : `backend/src/ari/domain/language_exercises.py` (aucun SDK, aucune dépendance de persistance).

| Champ | Valeurs |
|---|---|
| `level` | `B1`, `B2`, `C1` |
| `category` | `gender`, `case`, `verb_form`, `word_order`, `word_choice`, `register` (la liste fermée de l'évaluateur, sans `other`) |
| `skill` (déduit de la catégorie) | `grammar`, `vocabulary`, `register` |
| `format` | `cloze` (choix à trous), `correct_sentence` (correction d'une phrase fautive), `register_choice` (reformulation de registre) |
| `stem`, `options` (3 ou 4), `answer_index`, `explanation_fr`, `context_fr`, `setting` | la phrase, les options, la bonne, l'explication, la situation, le décor médical |

Tous les formats sont des choix multiples à **une seule** bonne option. La correction est donc une
comparaison d'indices (`grade`) : aucun modèle ne note un apprenant, et une réponse libre juste
n'est jamais comptée fausse. Les formats à réponse libre viendraient avec une mesure de leur
correction.

« Le LLM propose, le code décide » : l'auteur ne donne que la phrase, la clé, les distracteurs et
l'explication ; le **code** construit les options, les mélange avec une graine tirée du contenu (la
clé ne suit pas les habitudes du modèle), calcule le hash (`lx-<20 premiers hex>` est
l'identifiant) et refuse ce qu'il peut refuser.

## 2. Du modèle auteur à la banque

```
auteur (gpt-6-luna) ──► contrôles mécaniques ──► résolution à l'aveugle ──► juge strict ──► lot (fichier)
                         (code, sans modèle)       (Claude Sonnet 5.5,        (Claude Sonnet 5.5,        │
                                                    sans la clé)               grille v1, avec la clé)    ▼
                                                                         import : tout est revérifié,
                                                                         rapport de gate passé exigé ──► banque
```

Chaque étape ne peut que **retirer** un exercice, jamais le réparer.

1. **Contrôles mécaniques** (`mechanical_issues`, version `exercise-mechanical-v1`) : nombre
   d'options, clé dans les options, options distinctes (normalisation de `domain/text.py`), format
   permis pour la catégorie, un seul trou, rien d'un trou dans une correction, texte à corriger
   différent de la clé, options d'une correction proches les unes des autres, situation présente
   pour un registre, longueurs, caractères latins seulement, ni balise ni lien, aucune coordonnée
   personnelle (`clinical_privacy`). Un doublon (même phrase correcte normalisée) est retiré avant
   tout appel.
2. **Résolution à l'aveugle** (`exercise-solver-v1`) : le second modèle voit la consigne, la
   situation, la phrase et les options, **jamais la clé ni l'explication**. L'exercice reste si le
   modèle choisit la clé, déclare qu'**aucune autre option** n'est acceptable, et ne juge pas une
   phrase médicalement absurde (verdict grossier, sans avis clinique).
3. **Juge strict** (`exercise-judge-v1`, grille `exercise-rubric-v1`) : huit critères, un verdict
   chacun avec sa raison : `key_correct`, `distractors_wrong`, `targets_category`, `level_fit`,
   `explanation_correct`, `natural_register`, `medical_plausible`, `self_contained`. Le juge ne
   décide pas : le **code** approuve si et seulement si les huit passent. Un doute vaut un échec.
4. **Garde « autre modèle »** : la configuration est refusée si le contrôle est le modèle de
   l'auteur, et le service le revérifie sur le modèle enregistré par les appels eux-mêmes.

Décisions du plan appliquées : D3 modifiée (juge LLM aux consignes strictes, grille écrite et
versionnée, verdict par exercice stocké comme revue avec `reviewer_name` = modèle@grille, résolution
à l'aveugle de chaque exercice) et D5 (auteur `gpt-6-luna`, contrôle Claude Sonnet 5.5, par lots hors
ligne, aucun plafond en euros). Les modèles sont dans `evals/configs/exercises/luna-sonnet.yaml`
(dans le dépôt, donc versionnés ; pas de variable d'environnement, la production n'est pas touchée).
Les prompts sont dans `backend/src/ari/exercise_prompts/`, **hors de `prompts/`** : ils ne
changent pas l'empreinte IA de la production, qui conditionne `promote.yml`.

## 3. Stockage (base `platform`, migration `0007_language_exercises`)

- `language_exercises` : la banque. Le contenu ne change jamais (UPDATE d'une colonne de contenu et
  DELETE refusés par triggers). Seul le statut bouge, une fois, de `active` à `withdrawn` avec motif
  et date ; un exercice retiré n'est plus touché (jamais remis en circulation). Un exercice corrigé
  est un nouvel exercice. Unicité du hash et de la phrase correcte normalisée. Le trigger énumère
  ce qui peut changer (statut, motif, date), pas ce qui est protégé : une colonne ajoutée plus tard
  est immuable tant qu'une migration ne dit pas le contraire.
- `language_exercise_reviews` : en ajout seul, une ligne par contrôle franchi (`blind_solver`,
  `rubric_judge`, `human`, `learner_report`), avec le modèle et la version de la grille. Un retrait
  ajoute une ligne `human`.
- `language_drills` : en ajout seul, la réponse d'un apprenant (catégorie et niveau copiés pour le
  profil de langue, idempotente par `(learner_id, request_id)`). Le hash gardé doit être celui de
  l'exercice répondu (clé étrangère composite). Elle n'est lue par rien encore.
- Provenance de chaque exercice (colonne `provenance`) : lot, étapes (fournisseur, modèle demandé et
  modèle qui a répondu, version et hash du prompt), versions de la grille et des contrôles, rapport
  de gate qui a autorisé l'import.

La migration n'ajoute que trois tables, ne touche à rien d'existant et ne remplit rien. Son
`downgrade` supprime les trois tables : réservé à un retour arrière juste après la montée de
version.

## 4. Commandes

Outil local du propriétaire, comme la CLI du registre clinique (aucune requête d'apprenant n'y
accède). `--database-url` sinon `ARI_DATABASE_URL`.

```bash
# 1. Écrire un lot (appelle les modèles, facturé : --live ; ne touche pas la base)
PYTHONPATH=backend/src .venv/bin/python -m ari.exercises_cli generate \
    --config evals/configs/exercises/luna-sonnet.yaml --live \
    --spec B1:case:cloze:10 --spec B2:word_choice:correct_sentence:10 --out var/exercises/lot-1.json
# 2. Lire le lot (acceptés, refusés par étape, appels), puis l'importer
PYTHONPATH=backend/src .venv/bin/python -m ari.exercises_cli import var/exercises/lot-1.json \
    --gate-report exercise-gate/report.json
# Retirer, lister
... -m ari.exercises_cli withdraw <lx-…> --reason "…" --actor "Ismail"
... -m ari.exercises_cli list --level B1 --category case
```

Les clés `ARI_OPENAI_API_KEY` et `ARI_ANTHROPIC_API_KEY` doivent être dans l'environnement (ou le
`.env`) pour `generate --live`. Sans `--live` et avec la configuration `fake.yaml`, la chaîne tourne
sans clé sur un fournisseur simulé : elle ne mesure rien de l'allemand, et un lot simulé est **refusé
à l'import**.

Les exercices se consultent dans l'onglet **Exercices** du back-office (filtres par niveau,
catégorie, compétence, format et statut ; retrait par le propriétaire), voir `docs/BACKOFFICE.md`.

## 5. Le gate : aucun lot n'entre sans mesure

Question posée par le propriétaire (2 novembre 2026) : faut-il un workflow d'évaluation pour valider
ces changements ? Oui, et il bloque l'entrée dans la banque.

- Suite `language-exercises@1` (`python -m ari.evals exercises`, code dans
  `backend/src/ari/evals/exercises/`, jeu dans `evals/datasets/language-exercises/1/`). Un item est un
  exercice dont le défaut est **connu par construction** (ou qui est bon) : 16 exercices corrects écrits
  à la main et leurs variantes (mauvaise clé, deux réponses justes, aucune réponse juste, explication
  fausse, mauvaise catégorie, mauvais niveau, options en double, format cassé) plus 4 exercices au décor
  médical absurde ; 70 items en dev, 69 en holdout, familles disjointes. Le scorer est du code
  déterministe (le seul modèle appelé est celui des contrôles testés).
- Métrique principale : **taux de fausse acceptation** (`false_accept_rate`), l'exercice défectueux
  qui passe. Seuils de `gates.yaml` (`gates-v7`) : fausse acceptation ≤ 0, fausse rejection ≤ 0,25
  (valeur initiale, à recalibrer après la première mesure), contrôles mécaniques 1,0, verdicts
  changeants ≤ 10 %, structure exploitable ≥ 99,5 %. `false_accept` est une erreur critique : une
  seule nouvelle occurrence bloque.
- Workflow `Evals of the exercise checks` (`.github/workflows/evals-exercises.yml`, manuel, facturé,
  `main`, environnement `evals`, secrets `ARI_OPENAI_API_KEY` et `ARI_ANTHROPIC_API_KEY`). Artefact
  `exercise-gate`.
- `import` exige ce rapport (`--gate-report`) et refuse s'il ne correspond pas au lot : rapport pas
  du holdout, pas en mode gate, pas sur de vrais modèles, gate pas passé, **ou prompt, modèle de
  contrôle, grille, contrôles mécaniques ou fichier de configuration différents de ceux qui ont fait
  le lot** (`ari/evals/exercises/gate.py`). Le fichier `evals/configs/exercises/*.yaml` est haché dans
  le rapport et dans le lot, et il fige aussi `max_tokens` et `effort` du contrôle (jamais lus de
  l'environnement). Changer un prompt, un modèle ou un réglage impose donc un nouveau run avant le
  prochain import.
- L'import est tout ou rien : phrase déjà prise par un autre exercice, ou présente deux fois dans le
  lot, refuse le lot entier avant toute écriture (aussi avec `--dry-run`, qui annonce ce qui est déjà
  présent), puis un seul commit.
- Il ne fait pas partie de la promotion en production (`PROMOTION_RUNS`) : la banque est de la
  donnée, pas un déploiement.
- En CI, `make evals-fake` fait tourner la chaîne sur le fournisseur simulé (vérifie le câblage,
  pas la qualité).

## 6. Limites connues

- **Aucune mesure sur de vrais modèles n'a été faite** (pas de clé dans cet environnement). Les seuils
  sont des valeurs initiales. Le premier run du workflow est la première mesure, à lire avant tout
  import.
- L'allemand du jeu de test, des prompts et des exemples a été écrit par l'agent et **n'a pas été relu
  par une personne germanophone** (le propriétaire a retiré cette relecture, D3). Une base discutable
  peut produire un « exercice juste refusé » : regarder le détail avant de conclure.
- Seize bases : un taux de fausse acceptation de zéro n'est pas une garantie, c'est l'absence d'un
  défaut parmi ceux que le jeu sait fabriquer. Les fautes subtiles d'un modèle auteur (un distracteur
  presque acceptable) y sont moins représentées. Le filet qui reste, une fois la banque servie, est le
  bouton « Signaler une erreur » (après la PR 1).
- Les deux modèles de contrôle (résolution et juge) sont la même famille. Un défaut qu'elle ne voit pas,
  elle ne le voit ni comme solveur ni comme juge. Le tirage humain de 10 % reste facultatif (D3 modifiée).
- Le contrôle de plausibilité médicale est grossier par construction (décor, pas savoir).
- Limites relevées à la revue et gardées telles quelles : les contrôles mécaniques comparent les
  options sans tenir compte de la casse ni de `ß` (un exercice de registre « Sie / sie » est refusé
  comme doublon) ; une correction n'est pas vérifiée comme proche de la phrase fautive (le juge est le
  seul filet) ; la phrase correcte d'un exercice retiré reste prise, donc sa version corrigée doit
  changer de phrase ; `TRUNCATE` contourne les triggers par ligne (limite de toute la base).
- Hors périmètre de cette PR, volontairement : le tirage, le signalement, le créneau `language_drill`,
  le niveau de travail par catégorie, la génération en masse, l'API et l'interface apprenant.
