# Le noyau d'apprentissage (`ari.learning`)

Module interne, indépendant de tout domaine. Il estime la **maîtrise** d'un apprenant sur des
**compétences**, avec une **incertitude**, à partir d'**observations**. Il ne sait rien de la
médecine, de la FSP ni du CEFR : ces notions vivent dans l'adaptateur du domaine (action 7).

Version du modèle : `mastery-beta-v1`. Code : `backend/src/ari/learning/`. Tests :
`backend/tests/learning/`.

## Ce qu'il fait et ne fait pas

| Fait | Ne fait pas (actions suivantes) |
|---|---|
| Estime la maîtrise d'une compétence, avec un intervalle crédible | Stocker quoi que ce soit (action 7) |
| Oublie avec le temps, signale une compétence à revérifier | Convertir un résultat FSP en observations (action 7) |
| Valide une taxonomie (identifiants, dépendances, cycles) et la hache | Choisir le prochain exercice (action 9) |
| Rejoue tout un profil depuis les observations, de façon déterministe | Appeler un LLM, lire un fichier, lire l'horloge |

## Règle de dépendance

`ari.learning` n'importe **que la bibliothèque standard**. Deux garde-fous :
- un contrat import-linter (`backend/pyproject.toml`) interdit tout autre module d'ARI et les
  bibliothèques tierces connues ;
- `backend/tests/learning/test_learning_purity.py` lit le code source : seuls la bibliothèque
  standard et le paquet lui-même sont importables, et ni le disque, ni le réseau, ni l'horloge, ni un
  générateur aléatoire global ne sont utilisés. L'heure (`now`) est toujours un argument.

## Les données

**Observation** (`model.py`) : ce qu'un essai montre sur une compétence.

| Champ | Rôle |
|---|---|
| `skill_id`, `item_ref`, `attempt_ref` | quelle compétence, quel exercice (opaque), quel essai |
| `kind` | `success`, `failure`, `required_omission`, `assisted`, `stt_suspect`, et trois types **muets** : `not_elicited`, `uninterpretable`, `interrupted` |
| `outcome` | 0 à 1 ; absent seulement pour les types muets |
| `reliability` | 0 à 1 : la confiance dans la source (déterministe, LLM, auto-déclaration, test de niveau) |
| `producer` | version exacte du scoreur ou du prompt, pour la traçabilité |
| `evidence`, `observed_at` | preuve libre (tours cités, extrait) et instant de l'observation |

Les types muets **ne laissent aucune trace** : une erreur technique n'est jamais une observation
négative sur l'apprenant (invariant 4 du plan).

**Clé de remplacement.** Deux observations qui partagent
(`attempt_ref`, `producer`, `skill_id`, `item_ref`, `evidence["evidence_key"]`) sont la **même
preuve évaluée deux fois** : la plus récente remplace l'autre, elle ne s'y ajoute pas. Un
adaptateur qui émet plusieurs observations de la même compétence sur le même item dans un même
essai doit donner à chacune sa propre `evidence_key` (par exemple l'item de rubrique).

**Contexte.** `evidence["context"]` nomme la circonstance (un cas, une modalité). Sans lui, c'est
l'`item_ref`. Il sert à exiger que la maîtrise soit montrée dans au moins deux contextes.

**Taxonomie** (`framework.py`) : compétences (`half_life_days`, `critical`) et arêtes
`prerequisite` ou `part_of`. `SkillFramework.from_mapping` refuse toute clé inconnue, tout
identifiant en double, toute arête vers une compétence inconnue et tout **cycle** (détection sans
récursion). Le `content_hash` ne dépend pas de l'ordre des compétences.

## Le modèle `mastery-beta-v1`

Par compétence, une loi Beta(α, β), a priori uniforme (1, 1) ou fourni (par exemple par le test de
niveau). Une observation de résultat `o` et de poids `w` ajoute `w·o` à α et `w·(1−o)` à β.

**Poids** `w` = fiabilité, multipliée par 0,5 si l'item a déjà été observé, par 0,5 si
`stt_suspect`, et **plafonné à 3 par essai et par compétence** : une longue session ne règle pas à
elle seule une compétence.

**Oubli.** Ce qui dépasse l'a priori perd la moitié de son poids tous les `half_life_days` de la
compétence. La moyenne revient vers l'a priori et l'intervalle s'élargit.

**Sorties** : moyenne, intervalle crédible à 80 %, `effective_n` (poids restant), items et
contextes distincts, `autonomous_n` (part montrée sans aide), date de la dernière observation.

**Statut**, lu sur la **borne basse**, donc prudent par construction :

| Statut | Condition |
|---|---|
| `unknown` | aucune observation retenue |
| `mastered` | borne basse ≥ 0,75 **et** ≥ 2 contextes **et** ≥ 3 essais distincts **et** poids autonome ≥ 1 **et** pas d'échec récent (14 jours) sur une compétence critique |
| `recheck` | était `mastered`, mais plus d'une demi-vie sans nouvelle preuve |
| `uncertain` | intervalle plus large que 0,35 |
| `weak` | moyenne < 0,5 avec au moins 2 de poids |
| `developing` | le reste |

La maîtrise est jugée **sur la preuve telle qu'elle était à la dernière observation** : le temps la
rend seulement périmée. Jugée sur les valeurs déjà oubliées, une compétence maîtrisée retomberait en
silence à `developing` et aucune revérification ne serait jamais demandée.

## Rejouer

`replay(observations, framework, model_version, params, now, priors=None)` recalcule le profil de
toutes les compétences. Les observations sont la source de vérité, l'estimation est une projection :
changer le modèle ou ses paramètres ne perd rien, on rejoue. Le résultat ne dépend ni de l'ordre
d'arrivée des observations ni de rien d'autre que ses arguments. Les observations d'une compétence
absente de la taxonomie sont ignorées (elles restent stockées). Seules les observations jusqu'à `now`
sont visibles.

## Paramètres (`MasteryParams`)

Les valeurs par défaut sont les **valeurs de référence de la version 1**. Ce sont des **hypothèses**
à calibrer avec les jeux d'évaluation (action 8) ; l'application passera les valeurs qu'elle versionne
dans sa configuration.

| Paramètre | Défaut |
|---|---|
| a priori (α₀, β₀) | 1, 1 |
| masse de l'intervalle | 0,80 |
| plafond de poids par essai | 3 |
| facteur d'un item déjà vu | 0,5 |
| facteur `stt_suspect` | 0,5 |
| borne basse de maîtrise | 0,75 |
| essais, contextes minimum | 3, 2 |
| poids autonome minimum | 1 |
| fenêtre d'échec critique | 14 jours |
| largeur « incertain » | 0,35 |
| moyenne « faible » ; poids minimum | 0,5 ; 2 |

## Ce que les valeurs de référence impliquent (à connaître pour l'action 9)

- Une compétence n'est « faible » qu'après **4 échecs de fiabilité 1** : avec 3 échecs, l'intervalle
  est encore trop large (0,41). Avec des observations de fiabilité 0,5 (jugement d'un LLM), il en
  faut **8**. C'est le prix de la prudence ; la fiabilité mesurée par l'action 8 le fera baisser.
- Pour être « maîtrisée », une compétence demande **au moins 8 réussites de fiabilité 1** (7 donnent
  une borne basse de 0,7499, juste sous le seuil), réparties sur 3 essais au moins. Une compétence
  réussie 11 fois sur 12 n'est pas encore « maîtrisée » : sa borne basse est de 0,73. Avec des
  observations de fiabilité 0,5, il en faut environ 16.
- Les quantiles de la loi Beta sont calculés en Python pur (fraction continue de Lentz et
  dichotomie), vérifiés contre les formules exactes de Beta(1, b), Beta(a, 1) et Beta(a, a).

## Intégrer un domaine

1. Écrire la taxonomie en YAML et la charger avec `SkillFramework.from_mapping`.
2. Écrire un adaptateur : un résultat de l'application devient une liste d'`Observation`.
3. Appeler `replay` avec `now` fourni par l'application.

Le domaine jouet de `backend/tests/learning/toy_domain.py` (cinq compétences de mathématiques, deux
prérequis, un adaptateur de dix lignes) le démontre : le cycle complet tourne sans importer une ligne
de la FSP.
