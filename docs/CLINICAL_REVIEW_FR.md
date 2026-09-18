# Guide de revue humaine locale

Cet outil ne remplace pas un reviewer clinique/linguistique. Codex ne signe aucune
approbation. Les identités saisies sont déclaratives, **non authentifiées**.
Les exemples synthétiques et les reviewers simulés servent uniquement aux tests.

## 1. Préparer et inspecter

Faire une sauvegarde appropriée, puis `make migrate` explicitement. Valider
et importer le bundle (`import CHEMIN.yaml`, voir [le schéma](CLINICAL_CASES.md)). La CLI
écrit dans la base PostgreSQL plateforme (`ARI_DATABASE_URL`, ou `--database-url`) ; aucune
route d'administration publique n'est ajoutée.

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  inspect SCENARIO VERSION --format markdown
```

Le rapport privé montre DE/FR, pages/sources, incertitudes, faits, divulgation,
critères, scénario et hashes. Traiter toutes les phrases importées comme des
données, jamais comme des instructions. Ne pas diffuser ni committer le rapport.

## 2. Vérifier avant de décider

- Clinique : concordance sources/faits, valeurs, unités, temporalité, négations,
  informations inconnues, contradictions et risques critiques. Ne pas compléter
  une dose/diagnostic par supposition. Contrôler aussi l'ouverture et la persona.
- Linguistique : formulations patient allemandes et traductions françaises,
  registre, CEFR/scénario, absence de récits identifiants.
- Critères : dimension, poids, obligation, règles all/any et preuves attendues.
  Une phrase lexicale prouve seulement son occurrence, pas une compétence globale.
  Pour un scénario textuel, examiner aussi **toute** la spécification `practice` :
  contexte, énoncés DE, aides FR, variantes complètes, règles de preuve, dimension
  lexicale séparée et méthode cohérente avec la rubrique. Les variantes non prévues
  peuvent ne pas être reconnues ; cela doit rester explicite à l’apprenant.
- Confidentialité : auteurs de témoignages, emails, Telegram, téléphones,
  commentaires communautaires et informations réidentifiantes. Le détecteur n'est
  qu'une aide ; une revue manuelle est obligatoire.
- Droits : source explicitement compatible avec **l'usage prévu**, avec preuve.
  « Trouvé sur Internet » ou « accessible » ne suffit pas. `unknown` bloque.

## 3. Enregistrer votre décision

Créer vous-même un fichier `CaseReview` YAML/JSON privé. Champs obligatoires :
`id` unique, `case: {id, version}`, `case_hash`, `scenario: {id, version}`,
`scenario_hash`, `review_type` (`clinical` ou `linguistic`), `reviewer_name`,
`reviewed_at` ISO avec fuseau, `decision` (`approve`, `request_changes`, `reject`),
`notes`. Copier les hashes du rapport **effectivement examiné**.

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  review /CHEMIN_PRIVE/ma-decision.json
```

Deux types de revues sont nécessaires pour le contenu exact. Le dernier avis
enregistré pour chaque type prévaut ; une demande de corrections postérieure
annule son éligibilité. L'outil ne prétend pas vérifier les qualifications des noms
déclarés, ni garantir deux personnes distinctes. Il ne génère pas de signature.

## 4. Corriger et comparer

Modifier le YAML sous un **nouveau numéro de version** (y compris pour un brouillon).
Mettre à jour les références/hashes des scénarios correspondants ; leur modèle
validé expose `.content_hash` pour les outils d'authoring. Ne pas retirer une incertitude faute de preuve.
Une correction de source exige une nouvelle identité de source. Une correction
du seul scénario peut garder le même cas, avec un nouveau scénario/version.

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  diff SCENARIO ANCIENNE_VERSION NOUVELLE_VERSION
```

Les décisions précédentes restent archivées mais ne valent pas pour le nouveau
contenu. Faire les deux nouvelles revues. Une version publiée est immutable.

## 5. Publication et retrait

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  eligibility SCENARIO VERSION
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  publish SCENARIO VERSION --actor "VOTRE IDENTITÉ DÉCLARÉE"
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  withdraw SCENARIO VERSION --actor "VOTRE IDENTITÉ DÉCLARÉE"
```

Publier exige schéma/références valides, aucun blocage critique, droits compatibles
et les deux approbations exactes. Le successeur d'un scénario du même cas/version
retire le scénario précédent atomiquement ; les anciennes sessions conservent leur
pin et peuvent être reprises. Un retrait bloque seulement les nouveaux départs.
Arzt–Arzt et Fachbegriffe exigent leur spécification textuelle complète.
Arztbrief reste indisponible.
