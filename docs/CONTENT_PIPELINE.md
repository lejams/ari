# Pipeline de contenu : du PDF de protocoles au protocole gold

Le pipeline transforme des PDF de protocoles d'examen FSP (jusqu'à une centaine de protocoles
par fichier) en **protocoles gold** : des enregistrements structurés, corrigés par un médecin
et validés par le propriétaire de la plateforme. Les cas d'entraînement (`clinical-case-v3`)
en dérivent ensuite. Il vit dans le paquet `ari.content`, sur la base `content`, séparée de la
base plateforme : l'application apprenant n'en a jamais les identifiants.

## Étapes

1. **Ingestion** (`DocumentIngestion`). Le PDF est vérifié (en-tête `%PDF`, taille maximale
   `ARI_CONTENT_UPLOAD_MAX_BYTES`), stocké par son SHA-256 sous `ARI_CONTENT_STORAGE_DIR`
   (`documents/<aa>/<sha>.pdf`, jamais en base) et enregistré avec sa **déclaration** :
   provenance, usage prévu, consentement, droits (`unknown` | `incompatible` | `compatible`
   avec preuve), et ce que l'uploader sait du document (Land, ville, Ärztekammer, mois,
   spécialité). Un même fichier déposé deux fois est reconnu : aucune nouvelle tâche.
2. **Texte** (tâche `extract_text`). PyMuPDF extrait le texte page par page (`document_pages`).
3. **Segmentation** (tâche `segment_document`). Le texte part au modèle par fenêtres de 25 pages
   avec 2 pages de recouvrement (`segmentation-v1`). Le modèle rend, pour chaque protocole qui
   commence dans la fenêtre, ses pages et sa phrase d'ouverture copiée mot pour mot. Le code
   fusionne les fenêtres de façon déterministe (doublons du recouvrement écartés, chaque
   protocole prolongé jusqu'au début du suivant) et marque `too_long` tout segment de plus de
   12 pages, à corriger à la main.
4. **Extraction** (tâche `extract_protocol`, une par segment). Le texte du seul segment part au
   modèle (`protocol-extraction-v1`) qui rend un `ExtractionOutput` strict. Le code assigne les
   identifiants (`a01`, `q01`, `t01`, `u01`), applique la déclaration (le Land déclaré prime, la
   ville n'est jamais lue dans le texte), lance le détecteur PII (`pii-detector-v1`) et
   enregistre la version 1 du protocole au statut `extracted`.
5. **Relecture** (`ProtocolWorkflow`). `release` envoie un protocole en `doctor_review`. Le
   médecin corrige par `revise` (nouvelle version, l'ancienne devient `superseded`) puis décide :
   `approve` (bloqué tant qu'il reste une incertitude, une question critique sans réponse ou
   pas de difficulté), `request_changes`, `reject`. Le propriétaire décide ensuite : `approve`
   gèle un `GoldProtocol` (Land obligatoire, PII vide ou note de dérogation, droits non
   incompatibles), `request_changes` renvoie au médecin, `reject` clôt.

Chaque appel de modèle est tracé dans `ai_runs` (fournisseur, modèle, version et hash du
prompt, hash de l'entrée, usage, latence, erreur). Chaque transition écrit `protocol_events`,
chaque décision `protocol_reviews`. Ces tables, ainsi que `document_pages` et `gold_protocols`,
sont append-only par trigger ; `documents`, `document_segments` et `protocols` ne changent que
de statut.

## Le `ProtocolRecord` (`fsp-protocol-v1`)

Contrat strict et haché (`content/domain/protocol.py`) : source (document, segment, pages),
localisation (Land nullable en brouillon, ville, Ärztekammer, mois `AAAA-MM`, spécialité),
présentation du patient, items d'anamnèse par section canonique FSP (valeur, polarité
présent/absent/inconnu, citation courte pseudonymisée, pages), diagnostic, questions des
examinateurs par partie, résumé Arzt-Arzt, Fachbegriffe, issue, questions ouvertes au médecin,
incertitudes de champ, rapport de pseudonymisation, pédagogie (pièges graves, pièges,
difficulté, notes). Une valeur inconnue est `null` avec `polarity: unknown`, jamais une
absence déduite. Les coordonnées personnelles sont refusées à la validation.

`review_blockers(stage)` calcule la checklist déterministe qui bloque une approbation.

## File de tâches et worker

`jobs` est une table Postgres réclamée par `SELECT … FOR UPDATE SKIP LOCKED`. Le worker
(`python -m ari.worker`, `make worker`) prend une tâche à la fois, relance sur erreur avec
un délai de 30 s puis 60 s, la déclare `dead` au troisième échec, et reprend les tâches restées
`running` plus de 30 minutes. `run-jobs --once` de la CLI vide la file puis s'arrête.

## CLI

```sh
PYTHONPATH=backend/src .venv/bin/python -m ari.content.cli ingest CHEMIN.pdf \
  --land Bayern --provenance "Reçu de X le …" --consent "X accepte l'usage pour ARI" \
  --rights compatible --rights-evidence "Accord écrit du …"
PYTHONPATH=backend/src .venv/bin/python -m ari.content.cli run-jobs --once
PYTHONPATH=backend/src .venv/bin/python -m ari.content.cli list-protocols [--land Bayern]
PYTHONPATH=backend/src .venv/bin/python -m ari.content.cli show-protocol P-xxxxxxxx-000
PYTHONPATH=backend/src .venv/bin/python -m ari.content.cli ai-runs --document SHA
```

`--database-url` et `--storage-dir` remplacent `ARI_CONTENT_DATABASE_URL` et
`ARI_CONTENT_STORAGE_DIR`. Les sorties passent par la redaction des coordonnées.

## Mesurer la qualité sur un PDF réel

1. Déposer le PDF dans un dossier hors dépôt (`private-case-sources/` est ignoré par git).
2. `ingest` avec la déclaration la plus complète possible, puis `run-jobs --once`.
3. Comparer `list-protocols` au nombre de protocoles comptés à la main ; vérifier qu'aucun
   segment n'est `too_long` ; échantillonner dix protocoles avec `show-protocol` ; relire les
   détections PII ; noter les totaux d'`ai-runs` (tokens, latence).
4. Itérer les prompts en changeant leur version (`segmentation-v2`, …) pour que les mesures
   restent comparables.

Ordre de grandeur attendu pour 200 pages et 100 protocoles : environ 550 000 tokens en
entrée, 300 000 en sortie, une à deux heures avec un worker séquentiel.

## Fournisseurs

En mode `fake`, des handlers déterministes (`content/fake_handlers.py`) répondent aux deux
schémas à partir de marqueurs textuels (`Protokoll`, `Land:`, `Beschwerden:`), ce qui permet
d'exécuter le pipeline complet hors ligne et dans les tests. En mode `openai`, les opérations
`protocol_segmentation` et `protocol_extraction` utilisent `ARI_CONTENT_MODEL` avec
`ARI_CONTENT_TIMEOUT_SECONDS`.
