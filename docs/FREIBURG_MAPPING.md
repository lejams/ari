# Freiburg 0.1 : import privé et préparation de la revue

## Pourquoi un brouillon distinct du scénario exécutable

L'export réel ne fournit ni rubrique calibrée, ni dimensions/maxima, ni CEFR,
difficulté ou règles de preuve complètes. Une ouverture allemande est null.
Les 30 questions médecin-médecin n'ont pas de réponse attendue ni référence de fait.
Les convertir en comportements réussis à partir d'un fait livré serait incorrect.

`import-freiburg` adapte donc ce contrat à `freiburg-review-draft-v1`, une zone
**non exécutable** du registre. Il ne produit pas de `TrainingScenarioVersion`.
Il ne remplit aucun champ clinique manquant, ne choisit pas de CEFR, ne produit
aucune revue et ne publie rien. Le passage au bundle exécutable reste un travail
d'auteur guidé par les décisions humaines, pas une promotion automatique.

## Mapping réellement implémenté

| Source | Brouillon ARI | Limite conservée |
| --- | --- | --- |
| `case_id`, `version`, `status` | clé du brouillon, version, état contraint `draft_unvalidated` | aucune valeur publiée acceptée |
| titres, diagnostic rapporté, démographie, ouverture DE/FR | structure typée intégralement conservée dans `material.case` | null inchangés, diagnostic non validé |
| 207 faits | identités, concepts, catégories, valeurs DE/FR, nombres/unités, médicaments, temporalités, pages et formulations conservés | unknown reste unknown; « kein Kontakt » est un texte source, pas une absence de maladie |
| divulgation | les sept valeurs Work sont conservées telles quelles | aucune équivalence automatique avec le moteur ARI |
| 207 critères de recueil | références de faits vérifiées dans leur cas | poids provisoires; dimensions/all-any à définir humainement |
| 30 questions médecin-médecin | critères et questions source conservés | ni règle de scoring inventée ni activation de phase future |
| 47 entrées de terminologie | lexique global du lot et liens aux cas vérifiés | les doublons de mots entre cas ne sont pas fusionnés arbitrairement |
| 42 questions ouvertes | questions globales et par cas conservées, références vérifiées | contradictions non résolues |
| quatre sources | licence déclarée, URL, pages, statut du checksum et rôle conservés | `rights=unknown`, `original_verified=false` |
| fichiers JSON/XLSX | reçu avec chemins privés, deux SHA-256 distincts et date d'import UTC | aucun de ces SHA n'est le checksum du PDF |

Les champs inconnus, clés dupliquées, IDs dupliqués, références manquantes,
pages invalides/hors cas, poids non positifs, nombres non finis, unités inconnues,
doses contradictoires et statuts/validations incompatibles sont refusés.
Les affirmations du corpus ne deviennent pas des vérités médicales par cette validation.
La validation ne corrige pas automatiquement les formulations allemandes fragmentaires.

## Contrôle du classeur

L'inspection initiale utilise le skill Spreadsheets en lecture seule. L'importeur
effectue ensuite un contrôle reproductible avec un lecteur XLSX limité au format
de données observé : en-têtes, IDs, valeurs, nombres, booléens, médicaments JSON,
pages et listes comparés champ par champ. Les six onglets doivent correspondre,
sans lignes supplémentaires ou supprimées. L'ordre des colonnes peut varier ;
l'ordre des lignes doit correspondre à l'export. Les formules sont refusées, jamais
exécutées. Les champs présents uniquement dans le JSON restent conservés et validés.

Onglets reçus : `Sources!A1:K5`, `Cases!A1:AC11`, `Facts!A1:R208`,
`Assessment Items!A1:L238`, `Terminology!A1:G48`, `Open Questions!A1:F43`.

## Persistance et commandes

Migration **0005**, sans modifier les migrations déjà livrées :

- `clinical_draft_batches` : lot source structuré JSON/JSONB partagé, reçu privé et date UTC ;
- `clinical_draft_cases` : clés cas/version, hash du contenu à relire, FK vers le lot,
  état limité au brouillon ; aucun lien depuis une session ou le catalogue.

Les deux tables sont immuables (triggers SQLite/PostgreSQL). Import transactionnel,
conflit explicite même pour un brouillon; toute correction exige une nouvelle version.
Le hash du lot porte sur le JSON typé canonique complet; le hash du cas couvre sa
structure, les sources, les questions globales/du cas et les contraintes du mapping.
Ordre des clés et mise en forme ne changent pas ces hashes. Une modification de
source ou du matériau de revue les change. Les dates/chemins/SHA de réception sont
des preuves d'entrée, distinctes du hash sémantique. Un réimport sémantiquement
identique conserve le reçu initial et ne crée ni nouvelle ligne ni approbation.

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  import-freiburg /CHEMIN_PRIVE/ARI_Freiburg_cases_draft.json \
  --workbook /CHEMIN_PRIVE/ARI_Freiburg_cases_draft.xlsx --validate-only

# Base dédiée locale, migration explicite (pas la base applicative existante).
ARI_DATABASE_URL=sqlite:///var/freiburg-review.db PYTHONPATH=backend/src \
  .venv/bin/alembic upgrade head

PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  --database-url sqlite:///var/freiburg-review.db \
  import-freiburg /CHEMIN_PRIVE/ARI_Freiburg_cases_draft.json \
  --workbook /CHEMIN_PRIVE/ARI_Freiburg_cases_draft.xlsx --dry-run
```

Retirer `--dry-run` réalise l'import. `--validate-only` ne crée aucune connexion;
`--dry-run` n'écrit rien, même si le fichier de base n'existe pas. Le rapport sépare
fichiers reçus, validation structurelle, cas nouvellement importés, cas identiques,
revues clinique/linguistique, droits confirmés et publication.

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  --database-url sqlite:///var/freiburg-review.db inspect-freiburg --format markdown
```

Le rapport complet contient des données privées : ne pas le committer. Le JSON
inclut pour chaque cas son hash exact, DE/FR, pages, médicaments, faits/divulgation,
critères, sources et blocages. On peut comparer deux rapports privés avec un diff
local; les nouvelles versions ne remplacent jamais les anciennes.

## Travail humain restant

1. Revue clinique : vérifier les faits/pages, diagnostic rapporté, inconnus et
   contradictions; ne pas déduire une ascite, grossesse, dose ou durée absente.
2. Revue linguistique : vérifier allemand naturel, traductions FR et termes douteux;
   rédiger l'ouverture manquante uniquement après résolution clinique.
3. Droits : conserver « GPLv3 [Frei] » déclaré page 67, confirmer séparément les droits
   pour l'usage prévu. PDF non consulté ici; son checksum reste inconnu.
4. Confidentialité : confirmer le caractère simulé des noms rapportés. Aucune
   anonymisation garantie. Le matériau source reste privé et hors entraînement.
5. Auteur : calibrer les poids actuellement à 1, définir rubrique/dimensions,
   CEFR/scénario et divulgation, maintenir les phases futures indisponibles.
6. Préparer une nouvelle version du bundle ARI, puis employer le workflow de
   [revue clinique/linguistique](CLINICAL_REVIEW_FR.md) sur ses hashes exacts.
   La prise de notes sur les brouillons n'est pas une approbation de ce futur bundle.

**Cette reprise n'autorise aucune publication ni approbation automatique et ne lance
pas le Goal 5.**
