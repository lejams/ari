# Évaluations des sorties IA (`ari.evals`)

`ari.evals` mesure, de façon reproductible, la qualité et le coût des modèles utilisés par ARI.

- C'est le contrôle exigé avant tout changement de prompt ou de modèle en production (invariant 5 du
  plan).
- C'est aussi le mécanisme de « coût minimal sous contrainte de qualité » (invariant 11, action 8,
  point 5bis) : on retient la configuration la moins chère parmi celles qui passent tous les seuils.

Le code est dans `backend/src/ari/evals/`, les données dans `evals/` (hors de l'image Docker). Le
code de production n'importe jamais `ari.evals` (contrat import-linter).

**Aucun LLM ne note.** Les scorers sont du code déterministe. Ils comparent la sortie du modèle
testé à une réponse connue d'avance : par construction pour les jeux synthétiques, par l'annotation
pour les autres. Le seul modèle appelé est celui de la configuration testée.

## 1. Ce qui est mesuré

| Suite | Ce qui est exécuté | Ce que dit l'annotation | Jeu de données |
|---|---|---|---|
| `patient-leakage@1` | le patient réel : `PatientSimulator` (`patient-v3`, entraînement) ou `ExamPatient` (`patient-exam-v1`, chaîne d'examen) selon la configuration | type de réponse attendu, faits que la question demande légitimement | 80 questions sur 4 cas inventés (60 dev, 20 holdout) |
| `evaluation-language@1` | l'évaluateur réel `LLMBackedEvaluator` (`evaluation-v6`) | erreurs injectées : genre, cas, forme verbale, ordre des mots ; mot français ; artefact de transcription ; section oubliée | 120 transcripts synthétiques (90 dev, 30 holdout) |
| `evaluation-medical@1` | l'évaluateur réel | omissions exigibles et erreurs médicales relevées par le médecin | **en attente du médecin** (section 6 du plan) |
| `voice-sim@1` | toute la chaîne vocale, de bout en bout : sockets, segmentation de la parole, STT, patient, voix, accusés de lecture, évaluation finale | faits demandés par chaque question du script | 1 script de 12 questions sur le cas `ARI-DEV-FR` |

Les jeux de `patient-leakage` et `evaluation-language` sont générés par
`evals/datasets/build_synthetic_v1.py`. Tout y est inventé : patients, faits, phrases. Un test
reconstruit les fichiers et vérifie qu'ils sont identiques à ceux du dépôt : on ne modifie jamais
un jeu à la main, on crée une nouvelle version.

## 2. Configurations

Une configuration (`evals/configs/*.yaml`) décrit ce qui est testé : le fournisseur et le modèle de
chaque tâche, les prompts et le patient.

```yaml
id: luna-patient-sonnet-evaluation
description: Patient sur gpt-6-luna, évaluation sur claude-sonnet-5-5
provider: live              # « fake » : fournisseur simulé, sans routes
routes:
  patient: {provider: openai, model: gpt-6-luna}
  evaluation: {provider: anthropic, model: claude-sonnet-5-5}
prompts:
  patient: patient-v3
  evaluation: evaluation-v6
patient_engine: training    # ou « exam » (chaîne d'examen, prompt patient-exam-v1)
```

**Correspondance avec la production.** Le fournisseur réel est construit par le code de routage de
la production (`llm_provider_from_settings`). Ce qui est mesuré est donc exactement ce qui
tournerait. Chaque route correspond à deux variables d'environnement :

| Route | Fournisseur | Modèle |
|---|---|---|
| `patient` | `ARI_PATIENT_PROVIDER` | `ARI_PATIENT_MODEL` |
| `evaluation` | `ARI_EVALUATION_PROVIDER` | `ARI_EVALUATION_MODEL` |
| `fact_attribution` (facultative) | `ARI_FACT_ATTRIBUTION_PROVIDER` | `ARI_FACT_ATTRIBUTION_MODEL` |

Sans route `fact_attribution`, l'audit des faits suit le patient, comme en production. Adopter une
configuration, ou revenir en arrière, revient à changer ces variables. C'est une décision du
propriétaire (section 6 du plan, point 7).

Configurations fournies :

| Configuration | Fournisseur | Patient | Évaluation | Moteur du patient |
|---|---|---|---|---|
| `fake` | simulé (CI) | — | — | entraînement |
| `fake-exam` | simulé (CI) | — | — | chaîne d'examen |
| `luna` | OpenAI | `gpt-6-luna` | `gpt-6-luna` | entraînement (production actuelle) |
| `luna-exam` | OpenAI | `gpt-6-luna` | `gpt-6-luna` | chaîne d'examen |
| `terra` | OpenAI | `gpt-5.6-terra` | `gpt-5.6-terra` | entraînement (configuration de retour arrière) |
| `luna-patient-terra-evaluation` | OpenAI | `gpt-6-luna` | `gpt-5.6-terra` | entraînement |
| `luna-patient-sonnet-evaluation` | OpenAI et Anthropic | `gpt-6-luna` | `claude-sonnet-5-5` | entraînement |

Un test vérifie que chaque route a un prix dans la grille de prix du code (`pricing-v2`). Sans
prix, le coût du run serait inconnu.

## 3. Lancer une évaluation

Depuis la racine du dépôt :

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.evals run --suite patient-leakage@1 --config evals/configs/fake.yaml --repeat 3
```

- `--repeat N` : passages par item (1 par défaut, 3 en mode gate).
- `--out` : chemin du rapport. Par défaut `var/evals/reports/...`, dossier ignoré par git.
- `make evals-fake` lance toutes les suites sur le fournisseur simulé : entraînement, chaîne
  d'examen et `voice-sim`. La CI l'exécute à chaque commit.

**Vrais modèles.** Une configuration `provider: live` est refusée sans `--live`. Avec `--live`,
les clés sont lues dans l'environnement (`ARI_OPENAI_API_KEY`, `ARI_ANTHROPIC_API_KEY`). Chaque
appel est chiffré avec la grille de prix et inscrit dans le rapport, avec le modèle demandé et le
modèle que le fournisseur dit avoir utilisé.

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.evals run --suite evaluation-language@1 --config evals/configs/luna.yaml --repeat 3 --live
```

**Mode gate et holdout.** Le holdout n'est lu qu'en mode gate. Il ne sert jamais à mettre au point
un prompt.

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.evals run --suite evaluation-language@1 --config evals/configs/luna.yaml --split holdout --gate --live
```

- Chaque item est répété 3 fois, les seuils d'`evals/gates.yaml` sont appliqués, et la commande
  sort en erreur (code 1) si un seuil échoue.
- `--baseline <rapport>` limite le blocage aux erreurs critiques nouvelles par rapport à la
  configuration en place.
- Un rapport sur le holdout ne contient aucun texte des items.

## 4. L'apprenant simulé : `voice-sim@1`

Un programme joue l'apprenant sur la vraie chaîne vocale. Ce n'est pas un LLM mais un script fixe :
mêmes questions à chaque passage, donc des mesures comparables. Le script `voice-sim@1`
(`evals/datasets/voice-sim/1/scripts.jsonl`) contient :
- les questions attendues ;
- une question dont la réponse n'est pas dans le cas ;
- une question hors sujet ;
- une question dans une autre langue ;
- une interruption volontaire du patient ;
- une réponse dont la lecture n'est jamais confirmée.

Il tourne en entraînement (push-to-talk) et à l'examen (micro ouvert, chaîne
`ARI_EXAM_ENGINE=chain`).

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.evals voice --config evals/configs/voice/fake.yaml
```

**Cible en processus (CI).** La commande crée une base PostgreSQL jetable sur
`ARI_TEST_DATABASE_URL`, y publie `cases/dev` et la supprime à la fin.
- Sont réels : l'application, la WebSocket, le tampon push-to-talk, le segmenteur de parole de
  l'examen, l'orchestrateur, les patients et leurs gardes, la persistance, le crédit après lecture,
  l'évaluation finale.
- Sont simulés : le STT, le LLM et les voix. La voix de l'apprenant code le texte dans le signal :
  le segmenteur l'entend comme de la parole, et le STT simulé retrouve les mots exacts. Le patient
  répond donc bien à la question du script.
- Chaque session est jouée par un compte neuf : la règle « une session vocale à la fois » et le
  quota d'examens ne sont ni touchés ni contournés.

**Mesures par tour :**
- latence entre la fin de parole et le premier audio du patient, P50 et P95 par mode ;
- écart de transcription (taux d'erreur par mot, le texte envoyé étant connu) ;
- faits choisis par le patient comparés aux faits demandés, et inventions ;
- refus des questions hors sujet ou en autre langue ;
- arrêt de la lecture à l'interruption ;
- **crédit d'un fait seulement après accusé de lecture**, vérifié dans la base : un tour non
  confirmé ou interrompu ne doit rien créditer.

**Mesures par session :** coût total à partir des enregistrements d'appels (STT, patient, voix,
évaluation), durée, nombre de tours.

**Ce que la cible en processus ne mesure pas :**
- la latence réelle : le traitement est mesuré, mais pas le réseau, ni l'attente du segmenteur,
  qui ne s'écoule pas en temps réel ;
- la qualité de la reconnaissance vocale et des voix, puisque ces briques sont simulées.

C'est le rôle de la cible staging, décrite plus bas, qui n'est pas encore faite.

**Cible staging (à faire).** Même script, joué contre l'environnement de staging sur le réseau réel,
avec les vrais modèles, 20 sessions par configuration. Les deux moteurs d'examen seront couverts
(`ARI_EXAM_ENGINE=chain|openai_realtime`). Son prérequis, le compte d'évaluation, existe
(migration `0005_evaluation_accounts`, décision du propriétaire du 24 octobre 2026) :
- **un compte d'évaluation dédié**, marqué par un administrateur depuis le serveur :
  `python -m ari.backoffice_api.cli evaluation-account mark --email <adresse>` (aussi `unmark` et
  `list`). Le compte doit avoir terminé l'onboarding. Aucune route web ne pose ni ne retire la
  marque ;
- ce compte n'a **pas de quota d'examens**. Le quota reste inchangé pour tous les autres comptes.
  Le coupe-circuit d'abus optionnel, lui, s'applique encore s'il est configuré ;
- **chaque session qu'il démarre est marquée** (`sessions.evaluation_account`, visible dans la
  réponse de l'API sous `evaluation_account`). Elle reste hors du programme de la semaine, du
  modèle de l'apprenant, de la progression, du résumé des cas travaillés et du carnet de
  vocabulaire. Elle reste dans l'historique ;
- sur la page Coûts, les appels de ces sessions, et ceux d'un apprenant marqué qui n'ont pas de
  session (test de niveau), sortent de tous les totaux, de la médiane et des sessions récentes. Ils
  sont affichés à part sur une ligne, pour que la dépense ne disparaisse pas ;
- retirer la marque rend le quota au compte à partir de ce moment. Ses sessions d'évaluation passées
  restent exclues, et ne comptent pas dans le quota.

La cible staging devra vérifier `evaluation_account` sur chaque session créée et s'arrêter si la
valeur est fausse. Staging force aujourd'hui les fournisseurs simulés (`docs/DEPLOYMENT.md`) :
jouer les vrais modèles y demande une décision du propriétaire.

## 5. Lire un rapport

Un rapport est un fichier JSON. Exemples réels sur le fournisseur simulé :
`evals/reports/examples/patient-leakage-fake-dev.json` et `voice-sim-fake.json`.

| Champ | Contenu |
|---|---|
| `dataset` | version du jeu, empreinte du manifeste, partition, familles |
| `config` | routes, prompts, moteur du patient ; pour `voice-sim`, cible, modes et moteur d'examen (`voice`) |
| `versions` | versions et empreintes des prompts, empreintes des schémas de sortie, versions du scorer, des seuils et de la grille de prix, commit (`GITHUB_SHA` en CI) |
| `models_recorded`, `models_returned` | par opération : modèle demandé, et modèle que le fournisseur dit avoir utilisé |
| `metrics` | métriques globales. `null` = non mesurable, jamais zéro |
| `segments` | les mêmes métriques par famille de cas (par mode pour `voice-sim`) |
| `reliability` | précision mesurée des observations par source et producteur. Elle remplacera la fiabilité 0,5 de l'action 6 |
| `critical_errors` | `<item>:<type>` pour chaque erreur critique |
| `cost` | total, moyenne par cas, détail par opération. Un seul appel sans tarif rend total et moyenne `null` (`unknown_calls`) |
| `latency` | P50, P95 et maximum par opération ; pour `voice-sim`, aussi fin de parole → premier audio par mode |
| `gate` | en mode gate : chaque seuil (`passed`, `failed`, `not_measured`) et les nouvelles erreurs critiques |
| `items` | par item : verdict majoritaire, types d'échec, compteurs, et pour chaque passage les appels avec leur coût et le détail attendu/obtenu (absent pour le holdout) |

Principales métriques :

- `patient-leakage` :
  - `leak_rate` : faits non demandés révélés ;
  - `refusal.recall` : bon type de refus ;
  - `refusal.any_recall` : refus quel qu'en soit le type ;
  - `over_refusal_rate` : question légitime refusée ;
  - `invented_fact_rate` ;
  - `answer.recall` ;
  - sur la chaîne d'examen, `free_text_rejection_rate` : part des formulations libres du patient
    rejetées par le filtre. Les tours non cliniques (politesse, demande de répétition, hésitation)
    ne révèlent aucun fait et comptent comme une non-réponse.
- `evaluation-language` :
  - `language.precision.<catégorie>` et `language.recall.<catégorie>` ;
  - `stt_false_error_rate` : artefact de transcription reproché à l'apprenant ;
  - `evidence_hallucination_rate` ;
  - `category_confusion_rate` ;
  - `code_switch.*` ;
  - `forgotten_section.*`.
- `voice-sim` :
  - `leak_rate` ;
  - `invented_fact_rate` ;
  - `answer.recall` ;
  - `refusal.any_recall` ;
  - `turn_failure_rate` ;
  - `credit.violation_rate` ;
  - `interruption.success_rate` ;
  - `transcription.word_error_rate` ;
  - `latency.<mode>.p50_ms` et `latency.<mode>.p95_ms`.
- Pour toutes les suites :
  - `structured_after_retry` ;
  - `structured_first_try` ;
  - `stability.changed_rate`.

## 6. Comparer des configurations, qualité et coût

On lance la même suite, sur la même partition, pour chaque configuration, puis :

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.evals compare var/evals/reports/luna.json var/evals/reports/terra.json var/evals/reports/sonnet.json --out var/evals/comparaison.json
```

Le premier rapport est la baseline, c'est-à-dire la configuration en place. La commande :
- refuse des rapports qui ne portent pas sur le même jeu, la même version et la même partition ;
- donne, pour chaque candidat, l'écart de chaque métrique avec un intervalle à 95 % (bootstrap
  apparié sur les items, répétitions comprises) et la liste des items qui changent de verdict ;
- rejuge chaque configuration avec le `gates.yaml` actuel et écarte, avec la raison, celle qui rate
  un seuil ou introduit une nouvelle erreur critique ;
- classe les autres par coût moyen croissant par cas et **recommande la première**. Une
  configuration au coût inconnu n'est jamais recommandée.

Exemple : `evals/reports/examples/patient-leakage-compare.md` compare le patient d'entraînement au
patient de la chaîne d'examen, sur le fournisseur simulé. Les deux échouent sur `leak_rate`, il n'y
a donc pas de recommandation. C'est attendu : le fournisseur simulé ne mesure pas la qualité.

## 7. Seuils et erreurs critiques

`evals/gates.yaml` (version `gates-v2`) reprend les seuils du plan :

| Mesure | Seuil |
|---|---|
| Erreurs de langue, par catégorie | précision ≥ 0,85, rappel ≥ 0,70 |
| Fausses erreurs dues au STT | ≤ 5 % |
| Hallucination de preuve | ≤ 2 % |
| Fuite de faits | ≤ 2 % des tours |
| Verdicts changeants | ≤ 10 % |
| Structure exploitable | ≥ 99,5 % |
| Rappel des erreurs médicales critiques | ≥ 0,90 |
| `voice-sim` : latence examen, P95 | ≤ 4 s (plafond ; objectif 2,5 s, section 3) |
| `voice-sim` : interruption | fonctionnelle |
| `voice-sim` : crédit sans accusé de lecture | aucun |

Une métrique non mesurable fait échouer son seuil.

Erreurs critiques (`critical_outcomes`) :
- `patient-leakage` : `invented_fact`. C'est une proposition à confirmer, fondée sur « zéro
  invention » (action 3).
- `voice-sim` : `invented_fact` et `credit_without_ack`.
- `evaluation-medical` : un élément marqué `critical` par le médecin et non relevé. Le code ne
  décide jamais de ce qui est critique.

## 8. Ce qui attend le médecin : `evaluation-medical@1`

Le format et le score sont prêts (`scorers/evaluation_medical.py`) ; il manque les données. Une ligne
par transcript :

```json
{"id": "…", "family": "<cas>", "case_id": "<id dans cases.json>", "target_cefr": "B2",
 "turns": [{"sequence": 1, "doctor": "…", "patient": "…", "revealed_fact_ids": ["…"],
            "patient_response_kind": "sources"}],
 "annotation": {"annotator": "<médecin>",
                "required_omissions": [{"item_id": "<item de rubrique du cas>", "critical": true}],
                "medical_errors": [{"turn": 3, "description": "…", "critical": false}]}}
```

- Il faut 30 à 50 transcripts d'apprenants, avec leur consentement. Ce sont des données
  personnelles : **elles ne vont pas dans le dépôt**. On les range dans un dossier ignoré par git,
  par exemple `var/evals/datasets/evaluation-medical/1/`, et on lance avec
  `--datasets-dir var/evals/datasets`.
- Le manifeste doit dire `"synthetic": false` et décrire le consentement recueilli.
- Les erreurs médicales dans ce que dit l'apprenant ne sont pas détectables aujourd'hui : le schéma
  `session-evaluation-v4` n'a aucun champ pour elles. Elles comptent comme manquées. Ajouter ce
  champ changerait le prompt et le schéma : c'est une décision du propriétaire.

## 9. Limites connues

- Les phrases allemandes des jeux synthétiques ont été rédigées par l'agent. Elles doivent être
  relues par un germanophone avant de se fier aux seuils de langue.
- Le holdout de la suite langue est petit pour certaines catégories, et les intervalles le montrent.
- `voice-sim@1` n'a qu'un script, en français, sur le seul cas vocal publié par `cases/dev`. Les
  scripts en allemand viendront avec les cas allemands publiés.
- La cible staging de `voice-sim` et le moteur `openai_realtime` en processus ne sont pas faits.
- Le blocage de `promote.yml` sur un changement de prompt ou de modèle n'est pas branché, et le
  workflow manuel sur vrais modèles n'existe pas. Les deux relèvent du propriétaire : le premier
  bloquerait toute mise en production tant qu'aucune mesure de référence sur les vrais modèles
  n'existe, le second demande une clé dans les secrets GitHub.
