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

Les exercices se consultent dans l'onglet **Exercices** du back-office, avec les scénarios médicaux
(filtres par type, niveau, catégorie, compétence et statut ; retrait par le propriétaire), voir
`docs/BACKOFFICE.md`.

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
- **Version `@2` (3 novembre 2026), suite par défaut.** La première mesure sur de vrais modèles
  (dev, run 41660638291) a refusé 5 des 8 exercices justes (0,625 contre 0,25 admis). Le diagnostic des
  refus (`ari.evals diagnose`) attribue presque tous les refus à des défauts
  réels des exercices justes écrits à la main : niveau mal étiqueté, option ambiguë, contexte
  insuffisant. Le juge et les seuils ne changent pas ; ce sont les exercices justes du jeu de mesure
  qui sont réécrits : 14 par jeu au lieu de 8, niveaux B1 à C1 corrigés, familles neuves, **holdout
  neuf** (celui de `@1` n'a jamais été lancé mais ses refus ont été lus). Constructeur :
  `evals/datasets/build_language_exercises_v2.py`. `gates-v8` : mêmes seuils, nouveau bloc
  `language-exercises@2`. Procédure : mesure sur le dev, lecture du diagnostic, puis le holdout une
  seule fois en mode gate si les refus à tort passent sous 25 %. Toute modification d'un critère de la
  grille reste une décision du propriétaire. **Mesure dev du 3 novembre 2026 (run 41729176958) :
  fausse acceptation 0, contrôles mécaniques 1,0, fausse rejection 0,143 (2 sur 14, seuil 0,25).** Les
  deux refus étaient fondés (d13 : sujet en tête défendable après une conditionnelle sans « wenn » ;
  d09 : explication muette sur les mauvaises options, énoncé en « Du » face à des options en « Sie »).
  Avant toute mesure du holdout, d13 est remplacé par un cas de discours indirect (C1), et les explications de d09 et h09 couvrent les
  mauvaises options. Quatre remarques de la revue Codex avaient déjà fait remplacer des distracteurs
  défendables.
- **Contrôles `v2` et version `@3` (4 novembre 2026), suite par défaut.** Le holdout de `@2`, lancé
  une seule fois (run 41805202714), a échoué sur une fausse acceptation : `e-h05-choice-cloze:no_correct`,
  la bonne réponse remplacée par la faute de frappe « angeschlagt » (1 passage sur 306). Le résolveur
  avait choisi l'option la plus proche, et le juge avait lu la forme corrigée. Cause traitée comme
  une famille d'erreurs, pas comme une exception : (1) le résolveur (`exercise-solver-v2`) peut répondre
  `chosen_index = -1`, « aucune option correcte », sans jamais choisir la plus proche ; (2) la grille
  passe en `exercise-rubric-v2` avec un neuvième critère, `key_form_exact` (la réponse attendue est
  écrite exactement, orthographe et morphologie, dans la phrase complète, sans correction implicite ;
  les distracteurs peuvent être fautifs) — demandé par le propriétaire le 4 novembre 2026 ; (3) la
  `reason` du juge est le seul mot `ok` quand le critère passe (moins de jetons de sortie, donc moins
  cher) ; (4) option `check_passes: 2` ou `3` (`evals/configs/exercises/luna-sonnet-double.yaml`) :
  le même contrôle est refait avec les options dans un autre ordre (inversé, puis décalé), sans
  partager les verdicts, et le premier refus rejette l'exercice ; le coût ne double que si le premier
  passage accepte. Les seuils ne bougent pas. **Jeu `@3`** : le dev reprend toutes les familles de `@2`
  (dev et ancien holdout, qui n'est plus un test indépendant, `e-h05` reste en régression) ; nouveau
  défaut `near_miss_key` (la bonne réponse écrite de façon compréhensible mais fautive : lettre
  manquante, ajoutée ou permutée, terminaison tronquée), une variante par exercice à trou ou à
  corriger, avec l'exercice juste comme contre-exemple ; holdout **neuf** (14 familles `e-k01` à
  `e-k14` et 2 exercices au décor absurde, écrits sans lire de verdict des vrais modèles). Constructeur :
  `evals/datasets/build_language_exercises_v3.py` ; `gates-v9` : mêmes seuils, bloc
  `language-exercises@3`. L'allemand n'a pas été relu par un germanophone (décision D3 ;
  question posée au propriétaire pour les nouveaux exercices de référence). Le cache de prompt n'a pas
  été retenu : les prompts du résolveur et du juge font environ 500 et 900 jetons, sous le minimum
  de 1 024 jetons nécessaire pour qu'un préfixe soit mis en cache. Procédure de mesure : dev avec
  `luna-sonnet` puis `luna-sonnet-double` (chacun avec l'accord du propriétaire sur le coût), choix de
  la configuration, gel, puis le holdout `@3` une seule fois.
- **Mesures du 4 novembre 2026 et version `@4`, suite par défaut.** Dev de `@3`, un passage
  (run 41908249384) : fausse acceptation 0, `near_miss_key` 0 (`e-h05` refusé), fausse rejection
  0,18 (5 exercices sur 28, tous des doutes du juge). Dev, double passage (run 41908281245) : fausse
  acceptation 0, fausse rejection 0,39 : écarté, il refuse 6 exercices justes de plus sans rien
  rattraper. **Holdout `@3`, lancé une fois en mode gate (run 41912108307) : fausse acceptation 0,
  stabilité 0,048, contrôles mécaniques 1,0, mais fausse rejection 0,40 (17 passages sur 42), gate
  échoué** ; holdout grillé. Le holdout cache son contenu, la cause n'est donc pas lisible ; le
  diagnostic du dev montre trois refus sur cinq au critère `distractors_wrong` sur des distracteurs
  réellement défendables (`eingeführt`, `angesichts dem`, `kaum der Patient hatte…`) : le juge fait son
  travail, ce sont les références qui sont fautives. **Jeu `@4`**
  (`evals/datasets/build_language_exercises_v4.py`) : le dev reprend tout ce qui a été mesuré
  (versions 2 et 3, dont le holdout `@3`), le holdout est neuf (14 exercices justes `e-m01` à
  `e-m14`, 2 exercices absurdes) et **relu par un lecteur extérieur** (un modèle, à la demande du
  propriétaire) avant sa première mesure : explication fausse (« der » donné pour un nominatif féminin),
  variantes qui changeaient le sens ou ajoutaient une information, distracteurs déjà corrects, deux
  exercices qui n'isolaient pas leur point, consigne de registre sans excuses. Le texte relu est
  `/mnt/project-files/exercices/holdout-propose-v4.md`. Le `no_correct` d'un exercice de vocabulaire est
  un mot bien orthographié qui ne convient pas (`fotokopiert`), pour ne pas répéter la faute de frappe de
  son `near_miss_key`. `gates-v10` : mêmes seuils, bloc `language-exercises@4`. **Holdout `@4` lancé une fois le
  4 novembre 2026 (run 41933097123, un passage) : gate échoué de justesse sur la fausse rejection
  (0,262 pour 0,25, 11 refus sur 42 passages)** ; fausse acceptation 0 dans toutes les familles,
  contrôles mécaniques 1,0, verdicts changeants 0,039. Le holdout `@4` est grillé à son tour.
  **Grille `exercise-rubric-v3` (décision du propriétaire, 4 novembre 2026)** : le niveau n'est plus
  un critère oui ou non. Le juge **n'est plus informé du niveau annoncé** et donne le niveau qu'il lit
  (`estimated_level`, B1, B2 ou C1) ; le code compare (`level_gap`) : même niveau, l'exercice est
  gardé ; **un cran d'écart, l'exercice est gardé et enregistré sous le niveau du juge**
  (`level_relabeled_from` dans le lot, la catégorie n'est jamais recalée) ; **deux crans, refusé**
  (`level_fit`). L'import vérifie que le niveau enregistré est celui du juge et que l'étiquette de
  l'auteur en est à un cran au plus. Nouvelle métrique d'information, sans seuil : `relabel_rate`
  (exercices justes acceptés avec un niveau déplacé).
  **Mesures dev de la grille v3 (5 novembre 2026, config à Sonnet puis à Haiku, valides : structure
  exploitable ≈ 1)** : Sonnet (run 41990466763) fausse acceptation 0,018 (6 erreurs critiques, toutes
  `level_mismatch`, 0,24), fausse rejection 0,262 (11 sur 42), recalés 31 % ; Haiku (run 41990470896)
  fausse acceptation 0,026 (9 erreurs critiques : 8 `level_mismatch`, 1 `wrong_category`, e-k13),
  fausse rejection 0,190 (8 sur 42), recalés 36 %. Contrôles mécaniques 1,0 des deux côtés. Faute de
  frappe, mauvaise clé, deux bonnes réponses : aucune acceptée. Le journal n'affichait pas le niveau
  lu par le juge pour les niveaux échangés.
  **Décision du propriétaire (5 novembre 2026) : mesure du niveau à un cran, et Haiku 5.5 seul, en dev
  comme en holdout** (la config par défaut du CLI et l'option du workflow sont `luna-haiku`). Les
  exercices à niveau échangé (B1 et C1 permutés, deux crans) sont mesurés contre le **vrai niveau**,
  celui de l'exercice juste de la même famille : accepté et enregistré à au plus un cran du vrai
  niveau (la même imprécision qu'un exercice juste recalé), ce n'est plus une fausse acceptation ;
  enregistré à deux crans, c'en est une. `scorer` passe à `language-exercises-scorer-v3` ; métrique
  d'information `level_swap_kept_within_one_step_rate` ; le diagnostic dev affiche pour chaque
  fausse acceptation de niveau « annoncé, vrai, lu » et compte les échanges tolérés. Seuils
  inchangés.
  **Mesures dev sur Haiku 5.5 seul (5 novembre 2026)** : un passage (run 41995683122), puis trois
  répétitions (run 41999479170, 0,15 € environ) : fausse rejection 0,1825, verdicts changeants 0,0705,
  contrôles mécaniques 1,0, structure exploitable ≈ 1 (tous sous les seuils) ; **fausse acceptation 0,004
  (3 critiques sur environ 1 000 passages)**, toutes dans deux familles, `e-k13-order-correction`
  (mauvaise catégorie deux fois sur trois, explication fausse une fois) et `e-k12-choice-cloze`
  (mauvaise catégorie une fois). L'exercice juste de ces familles est lui aussi refusé par les contrôles
  (par Sonnet et Haiku pour `e-k13`) : lecture, leurs références sont défendables et les défauts bâtis
  dessus ne sont pas nets.
  **Jeu `@5` (décision du propriétaire, 5 novembre 2026)** : le dev est le jeu `@4` sans ces deux
  familles (elles restent dans `@4`, qui ne change jamais), plus les 15 familles du holdout `@4`, déjà
  lues par un lancement. **Holdout neuf** : 14 exercices justes `e-n01` à `e-n14` (B1 ×4, B2 ×5, C1 ×5 ;
  genre, cas, forme verbale, ordre des mots, choix de mot, registre) et 2 décors médicaux absurdes,
  écrits sans lire aucun verdict des vrais modèles, en évitant les défauts relevés dans les anciennes
  références : aucun distracteur qu'un locuteur natif pourrait accepter, étiquette de catégorie
  que l'exercice travaille nettement, registre où une seule option est polie et complète. 124
  items en holdout, 491 en dev. `gates-v11` : mêmes seuils, bloc `language-exercises@5`. Aucun lecteur
  germanophone ; une relecture extérieure du holdout est demandée avant sa première mesure, qui n'aura
  lieu qu'une fois, avec l'accord du propriétaire.
  **Relecture extérieure du holdout `@5` (5 novembre 2026, jamais lancé) et jeu `@6`** : un modèle
  extérieur a lu les 16 exercices. Verdict : les 14 clés sont justes et uniques en allemand standard, les
  2 absurdes sont à refuser. Défauts relevés : explications imprécises (`e-n01` : « der » est aussi un
  datif féminin, « die » aussi un pluriel ; `e-n05` : « die » est un nominatif ou un accusatif, pas
  seulement un accusatif), distracteur ambigu de `e-n13` (« darf beginnen die Untersuchung », ordre
  marginal mais attesté) et explication qui désignait le mauvais mot, niveau de `e-n13` surévalué
  (règle de B1 à B2), premier absurde étiqueté « cas » alors qu'il travaille le genre. Une version publiée
  ne change jamais : **`@6`** reprend le dev de `@5` octet pour octet et corrige le holdout (explications,
  distracteur remplacé par un ordre nettement faux, `e-n13` en B2, premier absurde en `gender`). `e-n13`
  n'ayant plus de niveau échangeable (seuls B1 et C1 le sont), le holdout passe à 123 items (8
  `level_mismatch` au lieu de 9). Le holdout reste facile pour le C1 : il mesure la détection de fautes
  et d'absurdités, pas des exercices C1 exigeants (noté dans le manifeste). `gates-v12` : mêmes seuils,
  bloc `language-exercises@6`, suite par défaut du CLI et du workflow. Le holdout `@6` n'est lancé qu'une
  fois, avec l'accord du propriétaire.
- Métrique principale : **taux de fausse acceptation** (`false_accept_rate`), l'exercice défectueux
  qui passe. Seuils de `gates.yaml` (`gates-v12`) : fausse acceptation ≤ 0, fausse rejection ≤ 0,25
  (valeur initiale, à recalibrer après la première mesure ; première mesure sur le dev du 3 novembre 2026, run 41660638291 : fausse acceptation 0, contrôles mécaniques 1,0, structure 100 % après retry, mais fausse rejection 0,625, donc gate non passé), contrôles mécaniques 1,0, verdicts
  changeants ≤ 10 %, structure exploitable ≥ 99,5 %. `false_accept` est une erreur critique : une
  seule nouvelle occurrence bloque.
- Workflow `Evals of the exercise checks` (`.github/workflows/evals-exercises.yml`, manuel, facturé,
  `main`, environnement `evals`, secrets `ARI_OPENAI_API_KEY` et `ARI_ANTHROPIC_API_KEY`). Artefact
  `exercise-gate`.
- Sur le split dev, le workflow ajoute un **diagnostic** au log et au résumé (`python -m ari.evals
  diagnose <rapport>`) : pour chaque exercice juste refusé, l'étape qui l'a refusé (contrôle
  mécanique, résolveur à l'aveugle, juge strict), la raison (autre option choisie, plusieurs réponses
  acceptables, décor jugé absurde, critère de la grille en échec avec la raison du juge), et les
  fausses acceptations. Il sert à comprendre un taux de fausse rejection trop haut avant de
  toucher un prompt ; le holdout n'en a pas, son contenu reste hors des rapports.
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
