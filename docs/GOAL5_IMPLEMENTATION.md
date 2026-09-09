# Goal 5 — MVP local terminé et vérifié

Objectif actif : accueil → choix d'exercice → entraînement → feedback → prochaine
étape observable. Le corpus Freiburg n'est pas une source d'exercices disponibles.
Le parcours MVP est implémenté et testé localement avec des données exclusivement
synthétiques. Cette livraison ne constitue pas une validation du corpus Freiburg,
une certification FSP, un déploiement ou une réussite de CI distante.

## État de départ vérifié

Checkpoint Goal 4 : `c0dd16f`. Appel patient, persistance, feedback, pin des versions,
publication contrôlée et invariant de livraison audio existent. Le frontend est
encore centré sur un appel, sans parcours d'accueil/historique/progression complet.
Les API d'historique utilisaient les identifiants sans preuve de possession.
Arzt–Arzt et Fachbegriffe sont des phases modélisées mais non exécutées.

Le rapport public du Goal 4 indique dix brouillons privés, aucune revue/droits
confirmés/publication. Pour ce chantier, **aucune lecture des exports ou de la base
privée Freiburg**, aucune migration ou modification des bases applicatives existantes.

## Décisions et étapes

1. Protéger le profil local existant : cookie secret HttpOnly/SameSite, hash en base,
   expiration serveur, révocation; pas d'authentification commerciale. Contrôles HTTP
   et WebSocket, historique par propriétaire. Migration additive 0006, bases isolées.
2. Ajouter une spécification d'exercice optionnelle au scénario versionné. Questions,
   aides et critères entrent dans son hash/revue. Les hashes sans cette extension
   restent identiques (golden tests). Aucune activation de phase implicite.
3. Exercices médecin–médecin et lexique déterministes : contexte issu des faits,
   questions séparées, réponses complètes/variantes rédigées explicitement. Le score
   de correspondance textuelle ne prétend pas évaluer toute la compétence clinique.
   Fachbegriffe a uniquement une dimension lexicale. Aucun nouvel appel IA.
4. Persister les tentatives et réponses avec versions/hashes exacts, idempotence,
   interruption/reprise et feedback figé. Démos synthétiques uniquement si un drapeau
   explicite de développement/test est activé; production refuse ces démos.
5. Brancher les futurs scénarios publiés via le registre existant. Refuser drafts,
   phases sans spécification et contenus retirés pour les nouvelles sessions, sans
   casser l'historique épinglé. Aucun assouplissement des gates de publication.
6. Relier accueil, phase/mode Training–Exam, historique, feedback et progression.
   Exam ne divulgue pas d'aides ni de variantes avant la fin. Comparaisons uniquement
   entre contenus/rubriques/méthodes/modes compatibles; absence de données != zéro.
7. Tests de bout en bout hors réseau, migrations isolées, régressions voix/livraison,
   Ruff/Mypy/frontend et revue indépendante. Pas de CI/push/déploiement automatique.

## Identité locale : limites explicites

Le cookie est une preuve de possession du profil, pas une identité civile. Il expire
après un an côté navigateur **et serveur**. `DELETE /api/profile` le révoque et retire
le cookie sans effacer les sessions. Sans cookie, un ancien ID en localStorage ne
permet pas de réclamer un historique. Aucun rattachement automatique aux profils
historiques non authentifiés; leurs données restent intactes. Pas de récupération
de compte, synchronisation multi-appareils ou authentification commerciale promise.

## Règles de preuve des exercices structurés

`practice-spec-v1` est inclus dans `TrainingScenarioVersion.practice`. Chaque question
référence un critère propre à cette phase, distinct des critères patient du cas.
Les variantes complètes sont définies dans `accepted_answers`, avec une preuve
`normalized_exact_answer`. Les mêmes faits/cas peuvent servir à plusieurs scénarios
sans duplication ni modification des critères vocaux.
`practice-exact-answer-v1` compare la réponse entière après casse/espaces/ponctuation
terminale seulement. Nombres et négations ne sont pas supprimés. Les formulations
non reconnues ne sont pas présentées comme médicalement fausses. Aucun fait n'est
crédité comme entendu via cette méthode : le scoring vocal reste séparé et inchangé.

Une dimension sans réponse est `no_data`/null. Un exercice incomplet ou synthétique
reste `provisional`; un exercice approuvé complètement répondu peut être `evaluated`
au sens de cette méthode limitée, pas certifié. Poids répondus et poids attendus sont
affichés séparément. Aucun score global ou niveau CEFR n'est déduit.

## Fonctionnalités implémentées

- Accueil avec profil local, objectif personnel B2/C1 explicitement non mesuré,
  explication Training/Exam, choix parmi les phases disponibles et état sans contenu.
- Arzt–Arzt : contexte issu des faits (y compris inconnu), présentation textuelle,
  question de suivi du médecin scripté, critères et variantes versionnés séparés.
- Fachbegriffe : termes du lexique versionné, explication en allemand courant,
  variantes explicites et dimension lexicale distincte.
- Mode Exam : aucun coaching, variante ou feedback intermédiaire avant clôture ;
  corrigé détaillé et prochaine étape uniquement après clôture irréversible.
- Reprise depuis l’historique ou une URL, pause, fin partielle, reçus idempotents
  contre les doublons et réponse réseau perdue. Brouillon de réponse en attente
  conservé dans sessionStorage, séparé par profil/exercice/question.
- Historique commun voix/exercices avec phase, mode, date, état et lien de feedback.
  Les modes vocaux historiques non enregistrés restent inconnus.
- Séries de progression distinctes par contenus/hashes/rubriques/méthodes/modes.
  Pas de moyenne ou score global. Les anciennes évaluations vocales v1 sont exclues
  des séries comparables ; leur feedback reste accessible dans leur session.
- Voix existante : versions, modèles, routage, livraison audio inchangés ; les aides
  lexicales sont refusées côté serveur pendant Exam. Nouveaux démarrages atomiques
  avec reçu idempotent ; anciens appels et feedback restent relisibles après retrait.
- Catalogue d’exercices branché au registre publié. Les deux revues exactes et les
  droits compatibles restent nécessaires. Aucun scénario sans spécification de phase
  ni Arztbrief n’est devenu exécutable. Tests de publication uniquement synthétiques.

## Exécution locale sûre

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.demo --port 8010
```

La démonstration s’ouvre sur `http://127.0.0.1:8010`. Elle utilise sa propre base
temporaire migrée, ignore `.env`, force le provider fake et ne publie aucun cas.
Ctrl+C arrête le serveur et supprime seulement cette base de démonstration.
Les deux assets de `practice_demos.py` sont versionnés, explicitement synthétiques,
avec droits inconnus et sans aucune revue. Le drapeau `ARI_ENABLE_MVP_DEMOS` vaut
false par défaut ; true est refusé en production. Aucun contenu vocal non approuvé
n’est exposé dans le catalogue produit. Les fixtures vocales historiques restent
accessibles uniquement au mode test de régression, sans mode produit explicite.

## Limites assumées

La présentation médecin–médecin est un dialogue **textuel scripté**, pas une IA de
raisonnement clinique ni une conversation vocale libre. La correspondance de réponse
est exacte après normalisation limitée : une reformulation correcte non rédigée peut
ne pas être reconnue. Cela est indiqué comme limite, jamais comme erreur clinique.
Il faut une rédaction/revue humaine des variantes pour des exercices réels.

Pas de récupération de profil, synchronisation, authentification commerciale,
publication Freiburg, certification, prononciation, Arztbrief, paiement ou déploiement.
Le contenu approuvé est un prérequis externe pour un usage réel ; le MVP démontrable
ne constitue aucune validation des dix brouillons privés du Goal 4.

## Audit de fin et preuves

| Exigence | État et preuve locale |
| --- | --- |
| Accueil → choix → entraînement → feedback → suite | Réussi dans `web/tests/practice-e2e.mjs`, avec profil B2 aspirational, les deux phases et le retour vers progression |
| Training / Exam | Tests API des deux modes/phases ; navigateur Exam sans aide avant clôture, Training avec indications et retour intermédiaire |
| Reprise, erreurs, doublons | Reload après pause, perte simulée des réponses HTTP de démarrage et d’envoi, retry sans doublon ; concurrence SQLite et PostgreSQL |
| Historique et isolation | Tests deux profils + anonyme, HTTP et WebSocket ; historique commun voix/texte ; cookie HttpOnly, expiration et révocation serveur |
| États sans contenu/données | Catalogue non publié vide ; voix non approuvée refusée ; score null/no_data avant réponse ; fin partielle provisoire avec poids répondus/attendus |
| Preuves et cohérence | Feedback pratique recalculé/vérifié contre le snapshot et les réponses avant commit et à relecture ; feedback forgé refusé ; versions de rubrique incompatibles exclues |
| Méthodes et progression | Méthode de rubrique liée au scénario ; tests de séparation de versions/phases/modes ; poids de critères (pas nombre brut de faits) ; pas de score global ni CEFR mesuré |
| Futur contenu approuvé | Tests import → deux revues synthétiques de test → publication → catalogue → session ; droits inconnus et phases sans spécification refusés ; retrait bloque seulement nouveaux départs |
| Démos et Freiburg | Démos explicitement synthétiques, flag off par défaut et refus en production ; aucun import/revue/publication runtime ; aucun accès aux exports ou à la base privée Freiburg dans ce chantier |
| Migrations et compatibilité | 0006–0008 additives sur bases isolées ; ancienne base simulée préservée et mode inconnu ; golden hashes historiques identiques ; JSONB/FKs/triggers vérifiés sur PostgreSQL 17 |
| Voix et livraison | Suite de régression conservée, contrôles de livraison/versions/stack et benchmark fake ; aucun modèle ou provider par défaut modifié |
| Revue indépendante | Findings publication de phase, cohérence feedback et rubrique corrigés ; dernière revue sans finding matériel |

### Résultats exécutés

- **222 tests backend réussis**, zéro skip, PostgreSQL réel inclus, sur l’état final.
  Un avertissement Starlette/httpx de dépréciation préexistant, aucun échec.
- Ruff backend : vert. Mypy strict : **82 fichiers source**, vert.
- `make test-web` : syntaxe JS, client pratique (retry/identité/no_data/erreurs),
  resampler PCM, livraison audio et sélection vocale : verts.
- Test navigateur Playwright/Chrome isolé : parcours des deux phases, feedback,
  progression, reprise, pertes HTTP, deux profils et affichage mobile : vert.
- Dépendances navigateur Playwright **1.62.1**, pnpm **11.19.0**, lockfile ;
  installation `--frozen-lockfile --ignore-scripts` testée localement.
- `make benchmark-smoke` : vert, mode offline uniquement, quatre stacks inchangées.
- `alembic check` SQLite et PostgreSQL inclus dans les tests : sans drift.
- `git diff --check` et parse du YAML CI : verts.

**CI distante non exécutée**, aucun push ou déploiement. Le workflow contient les
tests navigateur et PostgreSQL, mais une configuration locale ne prouve pas une
exécution GitHub Actions. Aucun test vocal payant ou benchmark micro réel nouveau.

### Fichiers et migrations

- Domaine : extension `clinical.py`, `practice.py`, mode vocal optionnel dans `models.py`.
- Application : ports/service pratique, projection publique sans fuite avant fin,
  progression comparable, paramètres de démarrage idempotent vocal.
- Infrastructure : catalogue publié et démos versionnées, persistance pratique,
  credentials locaux et contexte/reçu vocal ; migrations **0006, 0007, 0008**.
- API : `ownership.py`, `practice.py`, routes profil/historique/progression ; guards
  des aides Exam et des cas non publiés. Aucun endpoint administratif public ajouté.
- Interface : nouvel accueil `web/index.html`, modules `practice-*`, ancien espace
  vocal conservé dans `voice.html` avec accès à l’historique et feedback sans score global.
- Vérifications/docs : tests unitaires/API/PostgreSQL/navigateur, Makefile, verrouillage
  navigateur et CI, README, schéma clinique, guide de revue et migrations.

### Décisions encore humaines

Les dix brouillons Freiburg restent privés et non validés : aucune revue clinique,
linguistique ou confirmation de droits n’est réalisée par ce MVP. Leurs exports et
leur base n’ont pas été ouverts pour ce chantier ; leur statut antérieur est décrit
dans le rapport Goal 4, pas présenté comme une nouvelle inspection privée.
Un contenu réel utilisable exigera les décisions humaines et le workflow existant.
Les limites de comparaison textuelle et d’identité locale ci-dessus restent explicites.
