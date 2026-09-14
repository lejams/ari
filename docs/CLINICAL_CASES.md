# Cas cliniques versionnés

## Périmètre

Les cas utilisent `clinical-case-v2`, stocké dans le registre SQL après
import explicite. Aucune migration/import/publication n'est exécuté au démarrage.
Les brouillons n'apparaissent pas dans le catalogue. Une version retirée reste
accessible aux sessions historiques mais ne peut plus servir à une nouvelle session.

La phase `arzt_patient` est exécutable en voix ; `arzt_arzt` et
`fachbegriffe` le sont en texte uniquement avec une spécification `practice` valide
incluse dans le hash revu. Sans cette spécification, leur publication reste refusée.
`arztbrief` reste réservé et non exécutable. Les scénarios
peuvent évoluer indépendamment du cas. Un seul scénario est publié par version de
cas et phase ; publier son successeur retire automatiquement le précédent, avec
audit dans la même transaction. L'API peut préciser `scenario_id` et `scenario_version`.
Les reprises utilisent toujours le pin initial, jamais le nouveau scénario par défaut.

## Schéma YAML

Un document `ari-clinical-bundle-v1` contient `sources`, `rubrics`,
`terminology_sets`, `cases`, `scenarios`. Les ressources partagées sont référencées
par `(id, version, hash)` et ne sont persistées qu'une fois, même si plusieurs
bundles les utilisent. Les schémas exécutables sont dans `domain/clinical.py`.

- `RawCaseSource` décrit le document immédiat, provenance, date avec fuseau,
  checksum SHA-256 disponible, référence privée et droits pour un usage explicite.
- `ClinicalCaseVersion` contient langue, région, ville, phrases de réponse,
  sources/pages, faits, critères et questions non résolues.
- `ClinicalFact` conserve valeur typée, unité contrôlée, temporalité, présence,
  absence ou inconnu, criticité, formulations DE, traduction FR et incertitude.
  Une valeur manquante exige `value: null`, `polarity: unknown`, `unit: null`.
  Une absence doit être explicitement documentée. Aucune négation n'est déduite.
  Les unités acceptées sont une liste volontairement limitée, pas une ontologie
  médicale : mg, g, kg, mL, L, mmHg, bpm, °C, mmol/L, mg/dL, cm, mm, years, days,
  hours, %. Une autre unité exige une évolution explicite du schéma.
- `ClinicalAssessmentItem` décrit dimension, poids positif fini, obligation,
  comportement attendu et règle `all` ou `any`.
- `TrainingScenarioVersion` référence les hashes exacts du cas, de la rubrique et
  du lexique, puis définit persona, difficulté, CEFR, ouverture et objectifs.

### Exercices structurés

Le scénario peut porter `practice: PracticeSpecification` (`practice-spec-v1`) :

- `context_fact_ids` référence uniquement les faits réels du cas. Médecin–médecin
  exige au moins un fait de contexte et une première question `presentation`.
- `questions` sépare l’énoncé DE, l’aide Training FR, le type (`presentation`,
  `followup`, `term_definition`) et l’ID du critère. Chaque critère a une question.
- `assessment_items` porte des `PracticeCriterion`, distincts des critères vocaux
  du cas : dimension, poids positif, comportement attendu, variantes complètes
  `accepted_answers` et règle `normalized_exact_answer`.
- Fachbegriffe exige une question `term_definition` liée par `term_id` au lexique
  versionné et une rubrique contenant uniquement la dimension `lexical`.
- La rubrique et la spécification portent toutes deux
  `scoring_version: practice-exact-answer-v1`. Les rubriques vocales gardent
  `assessment-weighted-v1`. Une incohérence de méthode est refusée.

La nouvelle extension est omise de la représentation canonique lorsqu’elle est
absente : les anciens hashes/revues ne changent pas. Toute modification des questions,
variantes, aides ou critères impose une nouvelle version/hash de scénario et ses
deux nouvelles revues. Le rapport privé `inspect` contient toute la spécification.
Les critères patient/faits peuvent être partagés sans être réécrits pour une autre phase.

Le score textuel par dimension utilise les poids des questions **répondues** ; les
poids attendus sont affichés séparément. Absence de réponse = null/no_data, pas zéro.
Un exercice incomplet ou synthétique reste provisoire. Une formulation non reconnue
n’est pas déclarée fausse. Le mode Exam ne reçoit ni aide ni variantes avant clôture.
L’import seul ne rend pas un exercice disponible : droits et deux revues exactes,
puis publication explicite restent nécessaires. Le catalogue lit les publications,
sans modification de code ni promotion automatique des brouillons.

Les clés supplémentaires, clés YAML dupliquées, alias, clés non textuelles,
identifiants dupliqués, références inexistantes, hashes incorrects, unités sur
valeurs non numériques et nombres non finis sont refusés. Les champs textuels
et valeurs ne sont pas convertis implicitement depuis un autre type.

## Canonicalisation et identité

SHA-256 du JSON UTF-8, clés triées, séparateurs compacts, caractères Unicode
conservés, aucune valeur NaN/Infinity. Le hash v2 couvre **tous les champs du
modèle validé**, y compris les valeurs par défaut, l'identité/version, sources,
questions et traductions. L'ordre des listes et le contenu textuel sont significatifs.
Changer l'indentation ou l'ordre des clés YAML ne change pas le hash. Normaliser
l'espacement *dans une phrase* est une modification de contenu, pas de présentation.
Les statuts, revues et événements de publication sont des entités séparées, exclus
du hash clinique. Le hash du scénario couvre les références exactes des ressources.

Un identifiant est stable ; une version est une chaîne explicite, jamais un float
YAML (`version: "1.0"`). Le registre est append-only, **même pour les brouillons**.
Un même identifiant/version et hash est un réimport sans effet ; un autre hash est
un conflit. Une correction crée une nouvelle version. Les revues de l'ancienne
version restent auditées mais ne valent pas pour la nouvelle. Une source corrigée
reçoit un nouvel identifiant et les cas corrigés référencent cette nouvelle source.

L'import d'un bundle est une transaction unique, ressources avant scénarios.
Les clés étrangères SQL protègent sources, ressources, scénarios, revues et pins de
sessions. Des triggers SQLite de la migration initiale interdisent UPDATE/DELETE du contenu.
Le statut du scénario peut changer sans altérer son contenu. La publication et le
retrait sont sérialisés par verrou d'écriture du scénario (aussi sous SQLite),
et l'événement d'audit est committé dans la même transaction. Deux tentatives sur le même scénario donnent
un succès puis un refus métier « n'est plus un brouillon ».

Les sessions v2 ont un pin séparé vers le scénario/hash immuable, donnant les
versions/hashes exacts du cas, de la rubrique et du lexique. La création de session
revérifie le statut sous verrou pour éviter une course avec un retrait.

## Scoring `assessment-weighted-v1`

Chaque item est satisfait au plus une fois ; des répétitions de tours ou de faits
n'augmentent pas son poids. Dans chaque dimension :
`score = max_score × somme(poids satisfaits) / somme(poids définis)`.
Une dimension sans items retourne zéro par défense ; le schéma exige normalement
au moins un item par dimension. Les poids doivent être strictement positifs.
Les items obligatoires manqués sont listés, sans seuil de réussite implicite.

- `delivered_facts` : `all` exige tous les faits, `any` au moins un. Seuls les faits
  confirmés livrés, non interrompus,
  sont crédités. Un fait sélectionné ou un audio non confirmé ne suffit jamais.
- `doctor_quote` : recherche déterministe de phrases **écrites par le reviewer**
  dans les paroles du médecin (casse et espaces normalisés, limites de mots).
  `all`/`any` s'applique aux phrases attendues. Les références de faits sont du
  contexte et ne créditent aucun comportement. Les séquences de preuve sont gardées.

Cette règle lexicale n'est pas un jugement de compétence linguistique ou clinique :
elle ne comprend ni négation, ni paraphrase, ni pertinence contextuelle. Le reviewer
doit choisir des indicateurs observables et adaptés ; aucune note comportementale
n'est simplement déduite d'un fait entendu. Les retours qualitatifs du fournisseur
restent séparés des scores v2 déterministes. La méthode est versionnée et les
évaluations terminées sont relues sans recalcul.

## Limites de confiance

La CLI est locale et destinée à un opérateur autorisé. Le nom déclaré du reviewer
n'est **pas une authentification**. Les auteurs de cas n'obtiennent pas d'approbation
par un champ de statut dans leur export. Une personne disposant d'un accès SQL
administrateur peut contourner les outils/triggers : ce jalon n'est pas un système
d'autorisation multi-utilisateur. Aucune route d'administration publique n'est ajoutée.
