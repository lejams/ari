# Contrats d'export Work et Freiburg

Les deux exports réels ont été reçus et inspectés le 4 septembre 2026. Leur contrat
est `ari-clinical-cases-draft-0.1`, **pas** le bundle provisoire ci-dessous.
Utiliser `import-freiburg JSON --workbook XLSX` pour ces fichiers : voir
[FREIBURG_MAPPING.md](FREIBURG_MAPPING.md). Le classeur est réconcilié en lecture seule
avec une projection explicite des six onglets, sans heuristique ni IA.
Le schéma exécutable ARI et ses hashes historiques restent inchangés.

## Bundle ARI déjà préparé : work-ari-draft-v1

Le JSON machine attendu est un objet :

```json
{
  "export_contract": "work-ari-draft-v1",
  "training_bundle": {
    "schema_version": "ari-clinical-bundle-v1",
    "sources": [], "rubrics": [], "terminology_sets": [],
    "cases": [], "scenarios": []
  },
  "testimony_authors": [],
  "community_comments": [],
  "contacts": []
}
```

Les listes vides ci-dessus montrent la topologie, pas un corpus utilisable.
Un exemple complet, **synthétique et non validé**, est versionné dans
`cases/examples/synthetic_bundle.v2.yaml`. Le sous-dossier n'est pas chargé par
le catalogue legacy. Le schéma est décrit dans [CLINICAL_CASES.md](CLINICAL_CASES.md).

Les trois derniers champs sont optionnels et intégralement exclus du contenu
d'entraînement. N'y mêler aucune information médicale nécessaire : ranger faits,
pages et incertitudes dans les champs structurés prévus. Tout autre champ est refusé.
Les noms d'auteurs/récits identifiants doivent être séparés du scénario par l'auteur
de l'export et vérifiés par un humain ; l'outil ne sait pas reconnaître tous les noms.

Pour chaque source, renseigner identité stable, provenance, date d'import avec
fuseau, usage prévu, droits (`unknown` si inconnus) et PDF déclaré éventuel dans
`declared_original`. Le fichier Work réellement lu devient la source immédiate :
son nom, son SHA-256 (`immediate_source_checksum`) et son chemin privé sont enregistrés.
`original_checksum`, s'il est fourni, conserve le checksum **déclaré** du PDF original,
sans être remplacé par celui de l'export. Il reste non vérifié. `original_verified`
est forcé à `false` et `source_type` à `work_export`. Le PDF n'est jamais considéré
comme inspecté à partir du seul export.

La détection retire emails, liens/handles Telegram et certains formats de téléphone
des champs d'entraînement, puis ajoute une question **critique** de correction.
Elle conserve les nombres médicaux ordinaires (ex. `500 mg`, `3 jours`, âges).
Elle ne garantit pas l'anonymisation et peut produire des faux positifs : relire le
diff avant toute nouvelle version. Les matières premières restent privées.

Les hashes de références Work sont recalculés après adaptation/nettoyage, puis
le bundle complet est validé strictement par ARI. Ce recalcul ne produit aucune revue
et ne publie rien. Tous les scénarios insérés sont `draft_unvalidated` ; les champs
`published`, `status`, `reviews` sont refusés. Une absence clinique doit être
documentée ; une valeur manquante doit être `null/unknown`, jamais inventée.

## Commandes sans écriture

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  import /CHEMIN_PRIVE/bundle_ari_prepare.json --format work --validate-only

PYTHONPATH=backend/src .venv/bin/python -m ari.infrastructure.cases.cli \
  --database-url sqlite:///var/ari.db \
  import /CHEMIN_PRIVE/bundle_ari_prepare.json --format work --dry-run
```

`validate-only` ne construit aucune connexion de base. `dry-run` ne fait aucune
mutation et ne crée pas un fichier SQLite absent. Il compare avec les versions
persistées si la base existe ; sinon ses compteurs décrivent une base vide.
Le rapport français contient compteurs, hashes, nettoyage et questions critiques.
L'éligibilité finale inclut en plus droits et deux revues humaines.

Supprimer `--dry-run` effectue l'import dans une transaction unique. Réexécuter le
même fichier ne duplique rien. Une erreur tardive annule tout le bundle. Un même
ID/version portant un autre contenu est refusé, même avant publication : corriger
avec une nouvelle version. Si les octets de l'export source changent, utiliser une
nouvelle identité de source et de nouvelles versions des cas concernés.

Conserver les PDF et exports sous `private-case-sources/` ou hors dépôt. Les noms
d'exports Freiburg attendus sont ignorés par Git. Ne jamais forcer leur ajout ni
committer les rapports de revue contenant des références privées.
