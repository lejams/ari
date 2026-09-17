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

### Sections d'anamnèse et moments d'empathie

Le scénario (couche pédagogique, jamais le cas) peut porter deux extensions
optionnelles, omises de la représentation canonique quand elles sont vides : les
hashes et revues des scénarios existants ne changent pas.

- `anamnesis_sections` : liste de `{id, label_de, fact_ids}` où `id` appartient aux
  sections canoniques FSP (`patientendaten`, `aktuelle_beschwerden`, `vorerkrankungen`,
  `medikamente`, `allergien`, `noxen`, `familienanamnese`, `sozialanamnese`,
  `vegetative_anamnese`, `sonstiges`). Chaque fait appartient à au plus une section ;
  un fait hors section n'est simplement pas compté. L'évaluation calcule de façon
  déterministe (`anamnesis-sections-v1`) la couverture par section à partir des faits
  livrés, la première apparition de chaque section et le respect de l'ordre canonique.
  C'est une checklist, pas un jugement de raisonnement clinique.
- `empathy_moments` : liste de `{id, fact_id, cue_fr, expected_fr}`. Le déclencheur est
  déterministe : premier tour où le fait est livré ; la réponse jugée est le tour
  suivant de l'apprenant. Seul le verdict (`acknowledged`, `partial`, `ignored`) est
  demandé au fournisseur, une fois par moment déclenché, avec le tour de réponse comme
  preuve ; `not_triggered` et `not_reached` sont posés par l'application. Ces verdicts
  restent qualitatifs et n'entrent jamais dans les scores `assessment-weighted-v1`.

Trois « prochaines actions » au plus sont dérivées de signaux déterministes, dans cet
ordre : item obligatoire manqué, section non couverte, moment d'empathie ignoré ou
partiel, mots ajoutés au carnet.

### Test de niveau (`ari-placement-bundle-v1`)

Le test de niveau est du contenu de langue générale, pas un cas clinique. Il passe par le
même registre local : `placement import`, une revue **linguistique** du hash exact
(`placement review`), puis `placement publish` ; le contenu est immuable, seul le statut
change, et publier une version retire la précédente du même identifiant.

Un `placement-set-v1` porte `language`, `title`, `description_fr`, ses `sources` (droits
compatibles exigés) et trois familles d'items : `mcq_items` (`skill` vocabulaire ou
grammaire, `level`, `stem`, 3 ou 4 `options`, `answer_index` ; en pratique 6 à 8 items
par niveau évitent que le test s'arrête faute de questions), `listening_items` (`script`
lu par la synthèse vocale, jamais affiché avant la réponse, `question`, `options`,
`answer_index`) et exactement deux `speaking_items` (`prompt_fr`, `prompt_target`,
`target_seconds`). Au moins 4 QCM et 1 écoute par niveau A1 à B2 sont exigés.

Méthode `placement-staircase-v1`, déterministe : départ au niveau déclaré (sinon A2) ;
deux bonnes réponses consécutives montent d'un niveau, deux mauvaises descendent ; arrêt
après 14 questions, 3 renversements de direction ou épuisement des items du niveau
atteint (aucun emprunt à un autre niveau, qui biaiserait l'estimation) ; l'estimation
vocabulaire-grammaire est la médiane basse des niveaux des 6 dernières questions
présentées. Quatre écoutes autour de ce niveau donnent le niveau d'écoute (≥ 75 % de
bonnes réponses : +1 ; ≥ 50 % : égal ; sinon −1). Les deux productions orales sont
transcrites puis notées par le fournisseur (`placement-speaking-v1`, schéma strict :
niveau, confiance, observations citées). Le résultat global est le plus faible des
niveaux mesurés ; l'oral n'est compté que si sa confiance atteint 0,6. Il est copié dans
le profil comme `estimated_level`, avec la date et l'identifiant de la tentative. Ce
n'est ni un certificat ni le niveau requis pour l'inscription à la FSP.

### Programme hebdomadaire (`program-rules-v1`)

Le programme n'est pas du contenu et n'est jamais stocké : il est recalculé à chaque
lecture à partir du modèle apprenant (`learner-model-v1`, signaux déterministes
uniquement). Le scénario influence la recommandation par trois champs déjà revus :
`cefr` (un cas au plus un niveau au-dessus du niveau de référence est proposé),
`terminology` (recouvrement avec les mots dus du carnet) et `anamnesis_sections`
(sections faibles de l'apprenant). Un scénario sans ces champs reste recommandable,
seulement moins souvent.

### Progression par axes (`progress-axes-v1`)

`GET /api/progression` ajoute `axes` aux séries comparables : structure (carte sections ×
sessions sur les dix dernières sessions, moyennes, sections les plus faibles, respect de
l'ordre), communication (verdicts d'empathie, taux de réaction adaptée sur cinq moments
et sur les cinq précédents), langue (catégories fermées d'erreurs `gender`, `case`,
`verb_form`, `word_order`, `word_choice`, `register`, `other` sur cinq sessions, erreurs
récurrentes avec exemples cités), carnet (actif, acquis, ajouts et acquisitions sur trente
jours), rythme (sessions et minutes vocales par semaine sur quatre semaines) et niveau
(historique des tests). Tout est déterministe ; les catégories viennent du schéma strict de
l'évaluation (`evaluation-v6`), jamais d'un texte libre.

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
