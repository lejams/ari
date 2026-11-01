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
| `evaluation-language@3` | l'évaluateur réel `LLMBackedEvaluator` (`evaluation-v10`, vote sur 3 passes) | idem `@2` | 250 transcripts synthétiques : tout `@2` en dev (175), holdout neuf de trois familles (75 : genou, diarrhée, éruption cutanée) |
| `evaluation-language@2` | l'évaluateur réel `LLMBackedEvaluator` (`evaluation-v10`) | erreurs injectées : genre, cas, forme verbale, ordre des mots, choix du mot, registre ; mot français ; artefact de transcription ; section oubliée ; formulations correctes mais inhabituelles | 175 transcripts synthétiques (100 dev, 75 holdout) |
| `evaluation-language@1` | idem (version 1, gardée pour mémoire, plus exigée) | genre, cas, forme verbale, ordre des mots ; mot français ; artefact ; section oubliée | 120 transcripts (90 dev, 30 holdout) |
| `evaluation-medical@1` | l'évaluateur réel | omissions exigibles et erreurs médicales relevées par le médecin | **en attente du médecin** (section 6 du plan) |
| `voice-snippets@1` | la voix ElevenLabs de chaque persona sur des phrases fixes, puis le STT | termes critiques de chaque phrase (chiffres, doses, termes) | 33 phrases de patient en allemand |
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
  evaluation: evaluation-v10
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
| `luna-patient-haiku-evaluation` | OpenAI et Anthropic | `gpt-6-luna` | `claude-haiku-5-5` (même prix de catalogue que `luna`) | entraînement |

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
  d'examen, `voice-sim` et `voice-snippets`. La CI l'exécute à chaque commit.

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

## 4bis. La voix du patient sur des phrases fixes : `voice-snippets@1`

Avant de tester la voix en conversation, on l'écoute sur des phrases connues. Chaque phrase est
dite par la voix ElevenLabs de chaque persona (ou par des voix candidates), retranscrite par le
STT, puis comparée au texte par du code. Aucun modèle ne note.

Le jeu (`evals/datasets/voice-snippets/1/snippets.jsonl`) contient 33 phrases de patient en
allemand :
- les `patient_phrase` des cas inventés de `patient-leakage@1` ;
- les phrases difficiles du spike 3a : températures, tension, doses, dates, termes médicaux.

Chaque phrase liste ses **termes critiques** (chiffre, dose, date, médicament, terme médical),
avec les graphies acceptées.

**Comparaison.** Les deux textes sont normalisés de la même façon : minuscules, nombres écrits
en toutes lettres (« 38,5 » et « achtunddreißig Komma fünf » sont identiques), unités
abrégées développées (« mg »), traits d'union retirés. Verdict par phrase :
- `ok` : tout est entendu tel qu'écrit ;
- `listen` : les termes critiques sont entendus, d'autres mots diffèrent ;
- `critical_missed` : un terme critique n'est pas entendu ;
- `failed` : pas d'audio ou pas de transcription.

Un terme manqué désigne une phrase à écouter, pas une faute prouvée : le STT peut se tromper sur
une prononciation correcte. Le STT est appelé sans le contexte de transcription du cas, plus
strict que la production, pour ne pas masquer une mauvaise prononciation. Il n'y a pas de seuil :
c'est l'écoute qui tranche.

```bash
# Simulé (CI) : vérifie la chaîne, pas la voix.
PYTHONPATH=backend/src .venv/bin/python -m ari.evals pronunciation
# Vraie voix et vrai STT : ARI_ELEVENLABS_API_KEY et ARI_STT_API_KEY dans l'environnement.
PYTHONPATH=backend/src .venv/bin/python -m ari.evals pronunciation --live
# Comparer des voix candidates, en gardant les personas :
PYTHONPATH=backend/src .venv/bin/python -m ari.evals pronunciation --live --voice anna=<voice_id> --voice paul=<voice_id> --personas
```

- Sans `--voice`, les voix testées sont celles des personas, comme la chaîne d'examen les
  construit (`ARI_ELEVENLABS_EXAM_PERSONAS`, sinon `ARI_ELEVENLABS_VOICE_ID`). Les personas qui
  sonnent pareil forment une seule voix.
- Le STT est celui des réglages `ARI_STT_*` : Groq `whisper-large-v3` par défaut, comme en
  production. Le rapport nomme le service et le modèle utilisés.

**Sorties** (`var/evals/voice-snippets@1/<date>/` ou `--out-dir`) :
- `report.json` : par voix et par phrase, ce qui a été entendu, le verdict, le premier audio,
  le coût ;
- `a-verifier.md` : les phrases à écouter, avec le texte écrit et le texte entendu ;
- `audio/<voix>/<phrase>.wav` ;
- `ecoute/` : les mêmes fichiers sous des noms anonymes, dans un ordre mélangé, avec
  `grille.csv` (naturel, clarté, prononciation, crédibilité, de 1 à 5) et `index.html` pour les
  écouter ;
- `cle-ecoute.json` (hors de `ecoute/`, à ne pas montrer aux écouteurs) dit qui est qui.

**Workflow manuel `Patient voice on fixed sentences`** (`.github/workflows/voice-snippets.yml`),
sur `main`, dans l'environnement GitHub `evals` :
- voix par défaut : George (`JBFqnCBsd6RMkjVDRZzb`), une voix incluse ; vider `voices` pour tester les voix des personas ;
- secrets : `ARI_ELEVENLABS_API_KEY`, `ARI_STT_API_KEY` ;
- variables facultatives : `ARI_ELEVENLABS_EXAM_PERSONAS` (le JSON de la production),
  `ARI_STT_BASE_URL` et `ARI_STT_MODEL` pour un autre STT, par exemple OpenAI `whisper-1` ;
- l'artefact `voice-snippets` garde rapport et audio 30 jours.

Seules des phrases inventées sont dites : aucun audio d'apprenant n'est enregistré.

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

### Fiabilité de l'évaluateur de langue (31 octobre 2026)

Le premier run sur les vrais modèles (jeu v1, `evaluation-v7`, `gpt-6-luna`) a échoué sur quatre
mesures. Le diagnostic, exemple par exemple :
- le « 0 % de précision en choix du mot » venait de deux signalements sur une seule phrase
  (`Nennen Sie mich`, annotée `case`), classée tantôt `word_choice`, tantôt `case` selon la
  répétition : le jeu v1 n'injectait aucune erreur `word_choice`, donc le moindre signalement de
  cette catégorie donnait une précision nulle ;
- `Ihrem Familie` oscille entre `gender` et `case` ;
- trois signalements sur 85 portaient sur des passages sans erreur (`evidence_hallucination_rate`
  de 0,035 pour une limite de 0,02) ;
- les 23 % d'instabilité mélangent des changements de catégorie, une même erreur signalée deux fois
  et une remarque neutre comptée comme un changement.

Réponse, en trois couches, sans toucher aux seuils du plan :
1. **Mesure** (`evaluation-language-scorer-v2`, jeu `evaluation-language@2`) : la précision par
   catégorie n'existe que pour les catégories que le jeu injecte (les faux signalements d'une autre
   catégorie pèsent sur `language_overall.precision`) ; un même passage n'est rapporté qu'une fois (par l'évaluateur, pas par le scorer, pour ne pas
   masquer un second passage fautif) ; la
   signature de stabilité ne contient que les signalements jugés ; « bon endroit » et « bonne
   catégorie » sont mesurés à part (`language_location.precision|recall`,
   `category_accuracy_given_located`). Le jeu v2 injecte aussi `word_choice` et `register`, ajoute
   des phrases correctes mais inhabituelles (ellipse, question à l'ordre des mots d'une
   affirmation, salutation régionale) et un holdout neuf de trois familles. La famille
   `kopfschmerz`, ancien holdout vu pendant la mise au point, passe en développement. La règle
   d'annotation genre / cas est écrite en tête de `build_synthetic_v2.py` et dans le prompt.
2. **Prompt** `evaluation-v10` (v8 → v9 → v10, voir les mesures) : règles de catégories dans l'ordre, ce qu'il ne faut jamais
   signaler, citation exacte (`quote`) et correction (`correction`) obligatoires, parcours des tours
   un par un sans plafond de signalements, et les motifs qui sont toujours des erreurs (voir la
   mesure ci-dessous). Les exemples du prompt ne sont jamais des phrases du jeu de données.
3. **Code** (`application/services/language_errors.py`) : « le LLM propose, le code décide ».
   Une erreur dont la citation n'est dans aucun tour cité est écartée, la preuve est réduite aux
   tours qui contiennent la citation, une correction qui ne change que la ponctuation, la casse, les
   traits d'union, les espaces ou ss/ß est écartée (artefact du STT), un même passage (une citation contenue dans l'autre, dans le même tour) n'est
   rapporté qu'une fois, deux passages différents d'un même tour restent deux erreurs, une citation ne peut pas commencer ni finir au milieu d'un mot (`Ihre` n'est pas dans `Ihrer`), une correction qui ne fait que déplacer les mêmes mots est classée
   `word_order`, et un verbe à particule dit en un morceau (`einnehmen Sie`) que la correction coupe
   (`nehmen Sie … ein`) est classé `verb_form`.

**Mesures sur les vrais modèles** (`gpt-6-luna`, split dev, 3 passages, jeu `@2`) :

| | `evaluation-v8`, run 1 | `evaluation-v8`, run 2 | `evaluation-v9` | seuil |
|---|---|---|---|---|
| précision par catégorie / globale | 1,00 / 0,998 | 0,98 à 1,00 / 0,996 | 0,98 à 1,00 / 0,991 | 0,85 |
| hallucination de preuve ; fausses erreurs STT | 0 ; 0 | 0 ; 0 | 0,002 ; 0 | 0,02 ; 0,05 |
| rappel `word_choice` | **0,62** | **0,58** | 0,79 | 0,70 |
| rappel `word_order` | 0,75 | 0,74 | 1,00 | 0,70 |
| rappel `verb_form` | 0,87 | 0,84 | 0,96 | 0,70 |
| instabilité | **0,26** | **0,18** | 0,10 (pile à la limite) | 0,10 |

Lecture des 36 items à regarder du run 2 (commande `ari.evals diagnose`, dans le log du workflow) :
la précision n'est plus le problème. Le modèle **sous-déclare** : il rapporte deux ou trois erreurs
d'un transcript qui en contient davantage, toujours les plus évidentes, et le nombre rapporté varie
d'un passage à l'autre, ce qui produit l'instabilité (par exemple 1 erreur sur 3 au passage 1, 3 sur
3 au passage 2). Les manques constants sont des motifs précis : verbe conjugué ou participe mal placé
dans une question (`Haben Sie geraucht früher`), particule séparable absente (`Wie fühlt sich der
Schmerz`), mauvais auxiliaire (`Hat jemand … erkrankt`), collocation fausse (`lange Erkrankung`,
`stumpf` pour un mal de tête). Deux étiquettes restent ambiguës pour le modèle : préverbe mal placé
(`Was einnehmen Sie`, annoté `verb_form`) et article seul à changer (`in die Woche`, annoté `case`).
`evaluation-v9` répond à cela dans le prompt (parcours tour par tour, pas de plafond, motifs
toujours fautifs, règles d'étiquette), sans phrase du jeu de données dans les exemples, pour que le
split dev ne mesure pas une mémorisation. Résultat : tous les seuils passent sauf la marge
d'instabilité (10 items sur 100, pile à la limite). Les 10 items restants : un mot allemand correct
pris pour du français (`Migräne`, 2 items), deux « du » dans un même tour signalés deux fois, un
préverbe tantôt `verb_form` tantôt `word_order`, une particule oubliée vue une fois sur deux, et du
bruit sur des phrases limites. `evaluation-v10` ajoute trois règles générales (un mot correct n'est
jamais une alternance de langue, une erreur de registre par tour, contrôle de la particule de tout
verbe séparable) et le code tranche le préverbe coupé, pour avoir de la marge avant le holdout, qui
n'est lancé qu'une fois. Mesure de `evaluation-v10` sur le dev (31 octobre 2026) : tous les seuils
passent (`word_choice` 0,78, `word_order` 0,99, `verb_form` 0,95, précision globale 0,9965,
hallucination 0), mais l'instabilité reste à 0,10 pile : la règle du prompt n'a pas arrêté la fausse
alternance sur `Migräne` (4 items sur les 10 instables). Le code tranche donc aussi les
alternances (`verify_code_switches`) : le fragment doit se trouver dans le tour cité, et un terme
voulu identique au fragment dit n'est pas une alternance. Le diagnostic montre désormais
fragment et terme voulu de chaque alternance signalée.

La cause plus profonde est la variation du modèle d'un appel à l'autre : l'appel d'évaluation
n'imposait ni graine ni effort de raisonnement (réglages par défaut du modèle). Deux réglages
production, appliqués à l'évaluation de session sur OpenAI seulement :
`ARI_EVALUATION_REASONING_EFFORT` (`minimal`, `low`, `medium`, `high`) et `ARI_EVALUATION_SEED`.
Une route d'une configuration d'évaluation les porte (`reasoning_effort`, `seed`, visibles dans
l'étiquette de la route du rapport).

Mesures sur le dev (luna, graine 7, `evaluation-v10`, 1er novembre 2026) :

| | instabilité (≤ 0,10) | rappel global | précision globale | durée des 300 appels |
|---|---|---|---|---|
| sans réglage (référence) | 0,10 | 0,94 | 0,9965 | 18 min |
| `low` | 0,10 | 0,93 | 0,995 | 10 min |
| `medium` | **0,05** | 0,945 | 0,998 | 14 min |
| `high` (1re mesure) | 0,08 | 0,95 | 1,00 | 34 min |
| `high` (2e mesure) | **0,05** | 0,952 | 1,00 | 23 min |

Une première mesure de la seconde série avait échoué pour crédit épuisé (appels en échec, rapport
sans valeur) ; le diagnostic montre maintenant l'erreur du fournisseur. Retenu : `medium` avec la
graine 7 (autant de stabilité que `high`, plus rapide), fixé dans `docker-compose.yml` (donc dans
l'empreinte IA) et dans `evals/configs/luna.yaml`, la configuration de production du gate.

**Holdout du 1er novembre 2026 : échec, et il est grillé.** Un seul lancement (75 transcripts, trois
familles jamais vues) : instabilité 0,20 (≤ 0,10) et rappel `word_choice` 0,68 (≥ 0,70) ; précision
globale 0,97, hallucination de preuve 0,002, faux positif de transcription 0. La mise au point sur le
dev (instabilité 0,05) ne s'est donc pas transportée sur des familles neuves. Ce holdout ne sert plus
à rien : ni relance « pour vérifier », ni réglage sur ses résultats. Il faut un holdout neuf (nouvelles
familles, jeu `evaluation-language@3`) avant une nouvelle tentative du gate.

**Haiku 5.5 contre luna (dev, 1er novembre 2026)** : même instabilité (0,07 chacun), mais Haiku échoue
au rappel `word_choice` (0,68 contre 0,80), rappel global 0,89 contre 0,95, erreurs de cas 0,85. Même
prix sur la grille. Luna reste le modèle de l'évaluation.

**Holdout neuf `evaluation-language@3`** (`evals/datasets/build_synthetic_v3.py`) : le dev reprend
tout `@2` (son ancien holdout compris, puisqu'il est connu), le holdout est fait de trois familles
neuves (`knieschmerz`, `durchfall`, `hautausschlag`), avec leurs propres cas et phrases, jamais
montrées pendant la mise au point. Le gate de promotion passe sur `@3` (`gates-v5`, mêmes seuils
que `@2`). Règle fixée d'avance par le propriétaire (1er novembre 2026) : ce holdout est lancé une
fois ; s'il échoue seulement sur l'instabilité et le rappel, précision et hallucination passant, les
seuils deviennent 0,15 (instabilité) et 0,65 (rappel) et la version est promue, sans nouvelle
boucle de mise au point.

**Vote à la majorité** (`ARI_EVALUATION_PASSES`, 3 par défaut, de 1 à 5). Une passe unique oscille
entre signaler une erreur et la manquer ; c'est ce qui fait l'instabilité des items limites. Le
`LLMBackedEvaluator` appelle maintenant le modèle `passes` fois en parallèle (graine décalée de
l'indice de la passe pour que les passes ne se répètent pas), vérifie chaque réponse avec les mêmes
gardes de code, puis garde les erreurs de langue et les alternances de langue signalées sur les
mêmes mots par plus de la moitié des passes (2 sur 3 ; la catégorie est celle que la plupart ont
donnée). Tout le reste (résumé, points forts, empathie, vocabulaire) vient de la première passe. Une
passe en échec est écartée (la majorité se calcule sur les passes qui ont répondu) mais son appel est
enregistré pour le coût ; si toutes échouent, l'évaluation échoue comme avant. Coût : trois appels
d'évaluation au lieu d'un (environ 0,002 $ par session au prix de la grille). La configuration de
mesure dit `evaluation_passes: 3` (`luna.yaml` et `luna-exam.yaml`, que le test de promotion
compare au défaut de production) ; les autres configurations mesurent une seule passe.

**Mesures avec le vote (1er novembre 2026).** Dev `@2` (run 41323425764) : instabilité 0,05,
précision 1, rappel global 0,95, rappel `word_choice` 0,80, aucune hallucination de preuve, 900 appels
sans échec. **Holdout neuf `@3`, lancé une seule fois (run 41326682114, commit `9595bbc`) : gate
passé, sans changer aucun seuil.** Instabilité 0,027, précision 1 dans toutes les catégories, rappel
global 0,996 (`word_choice` 1, `case` 0,98, `verb_form` 0,99), hallucination de preuve 0, faux positif
de transcription 0, alternances de langue précision et rappel 1. `patient-leakage@1` passe sur les
deux configurations (fuite 0, fait inventé 0). Verdict de promotion : `passed`. La règle de repli sur
les seuils (0,15 et 0,65) n'a pas servi ; `gates-v5` reste en vigueur.

**Choisir la configuration** : le workflow manuel `Compare evaluation configurations`
(`.github/workflows/evals-compare.yml`, n'importe quelle branche, split dev uniquement, jamais de
verdict) lance la suite sur chaque configuration et les compare. On retient la moins chère qui passe
tous les seuils, puis on lance le holdout **une seule fois** avec `Evals on real models`. Le holdout
ne sert pas à itérer.

## 7. Seuils et erreurs critiques

`evals/gates.yaml` (version `gates-v5`) reprend les seuils du plan :

| Mesure | Seuil |
|---|---|
| Erreurs de langue, par catégorie injectée | précision ≥ 0,85, rappel ≥ 0,70 |
| Erreurs de langue, précision globale (`@2`, ajout à valider par le propriétaire) | ≥ 0,85 |
| Fausses erreurs dues au STT | ≤ 5 % |
| Hallucination de preuve | ≤ 2 % |
| Fuite de faits | ≤ 2 % des tours |
| Inventions du patient (`invented_fact_rate`) | ≤ 2 % des tours (décision du propriétaire, 24 octobre 2026) |
| Verdicts changeants | ≤ 10 % |
| Structure exploitable | ≥ 99,5 % |
| Rappel des erreurs médicales critiques | ≥ 0,90 |
| `voice-sim` : latence examen, P95 | ≤ 4 s (plafond ; objectif 2,5 s, section 3) |
| `voice-sim` : interruption | fonctionnelle |
| `voice-sim` : crédit sans accusé de lecture | aucun |

Une métrique non mesurable fait échouer son seuil.

Erreurs critiques (`critical_outcomes`) :
- `patient-leakage` : aucune. Le propriétaire a décidé le 24 octobre 2026 qu'une invention n'est
  pas critique : elle compte dans `invented_fact_rate`, avec un seuil.
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
- Le gate de promotion ne voit que ce que le dépôt fixe. Un modèle choisi dans le `.env` du serveur
  (`ARI_PATIENT_MODEL=...`) lui échappe : changer de modèle en production passe par
  `docker-compose.yml`, ou par un run de `evals-live.yml` sur une configuration qui le décrit.
- Les runs gate de la promotion n'ont pas de baseline : toute erreur critique bloque, même déjà
  présente en production.

## 10. Gate de promotion

Le plan (action 8, point 6) demande un gate dans `promote.yml` dès qu'un prompt, un modèle ou une
politique change, détecté par empreinte. Code : `backend/src/ari/evals/promotion.py`.

**Empreinte IA** (`python -m ari.evals fingerprint [--root <checkout>]`). SHA-256 de :
- chaque fichier de `backend/src/ari/prompts/` ;
- les versions de prompts que chargent les conteneurs de production (`container.py`), pour voir
  un retour à un ancien fichier ;
- les valeurs par défaut des variables modèle, fournisseur, effort et moteur de
  `docker-compose.yml`, ce que la production exécute (un test vérifie que chaque réglage IA de
  `Settings` y passe) ;
- la politique de routage, `infrastructure/providers/llm_routing.py`.

Une voix, une clé, un délai ou un fichier sans rapport ne la changent pas.

**Runs exigés** (`PROMOTION_RUNS`) : `patient-leakage@1` sur `luna.yaml` (entraînement) et
`luna-exam.yaml` (chaîne d'examen), `evaluation-language@3` sur `luna.yaml`. Tous sur le holdout,
en mode gate, sur les vrais modèles. Un test vérifie que ces configurations ont les modèles, les
fournisseurs et les prompts de production. `evaluation-medical@1` s'y ajoutera avec les annotations
du médecin, `voice-sim@1` avec sa cible staging.

**Workflow manuel `Evals on real models`** (`.github/workflows/evals-live.yml`), sur `main`
seulement, dans l'environnement GitHub `evals` :
- secrets à créer dans cet environnement : `ARI_OPENAI_API_KEY` (obligatoire),
  `ARI_ANTHROPIC_API_KEY` (pour une configuration Claude) ;
- `split: holdout` : le gate, qui écrit `verdict.json` (`passed` ou `failed`) ;
- `split: dev` : une mesure en 3 répétitions, dont le verdict est `measurement` et ne permet jamais
  une promotion ;
- l'artefact `eval-gate` garde les rapports, le verdict et l'empreinte 90 jours.

En local, la même chose : `python -m ari.evals promotion --live --out-dir <dossier>`.

**`promote.yml`.** Avant le transfert en production :
1. calcule l'empreinte du commit candidat, avec le code de `main` ;
2. la compare à celle de la dernière mise en production réussie (artefact `production-release`,
   gardé 90 jours) ;
3. si elle est identique, rien à faire ;
4. sinon, exige `eval_run_id` : un run réussi de `Evals on real models`, lancé à la main sur un
   commit de `main`, dont le verdict est `passed` pour exactement cette empreinte.

Conséquences :
- la première mise en production après la fusion de ce gate exige un run, faute d'empreinte
  enregistrée ;
- au-delà de 90 jours sans mise en production, l'artefact a expiré : un run est de nouveau exigé ;
- les seuils sont ceux du plan, à recalibrer après la première mesure. Si le gate échoue sur les
  vrais modèles, aucune mise en production n'est possible tant que la qualité ou les seuils ne
  sont pas revus.
