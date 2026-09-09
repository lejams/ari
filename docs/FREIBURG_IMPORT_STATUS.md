# Freiburg — état réel au 4 septembre 2026

## Résultat de la reprise du Goal 4

| Étape | État vérifié |
| --- | --- |
| Fichiers reçus | 2 : JSON + XLSX réels, lus sans modification |
| Concordance JSON / XLSX | 6 onglets, comparaison champ par champ réussie |
| Cas importés | **10**, état `draft_unvalidated`, dans la zone privée `clinical_draft_cases` |
| Réimport | 10 identiques, 0 doublon, 0 nouvelle écriture |
| Cas prêts pour revue humaine | 10, rapport DE/FR avec sources, hashes et blocages |
| Revues cliniques effectuées | **0** |
| Revues linguistiques effectuées | **0** |
| Cas aux droits confirmés | **0** |
| Scénarios exécutables créés | **0** |
| Cas publiés / approuvés automatiquement | **0 / 0** |

L'import source est terminé; le corpus n'est **pas validé ni publiable**.
Les brouillons ne sont ni des `ClinicalCaseVersion` exécutables ni des scénarios
utilisables en session. Aucun CEFR, rubrique, preuve comportementale ou champ clinique
manquant n'a été inventé pour forcer le contrat d'exécution.

## A. Infrastructure technique

Les acquis versionnage/revue/publication et sessions des Goals 1–4 sont préservés.
La migration additive `20260904_0005` ajoute deux tables de réception privée,
JSON/JSONB et immuables. L'adaptateur du **format réellement reçu** est distinct
de l'ancien contrat provisoire `work-ari-draft-v1`.
Voir le [mapping détaillé et les commandes](FREIBURG_MAPPING.md).

## B. Corpus réellement importé

Exports de l'emplacement privé explicitement autorisé par l'utilisateur :

- `ARI_Freiburg_cases_draft.json` : SHA-256
  `0d3352172537cfb7b8b11b1d21f8a296138f2b35505c78d841d30298def8cfcb` ;
- `ARI_Freiburg_cases_draft.xlsx` : SHA-256
  `3cb1eaab52d1d7a4d69d18f0b8664af10a3978d2a9b3161e239700a10267b9f6`.

Empreintes contrôlées avant et après import : identiques. La source immédiate
reste l'export Work. Source originale **déclarée**, non consultée ici : PDF FSP
Freiburg fourni sur Google Drive, pages disponibles conservées dans les données.
La déclaration **« GPLv3 [Frei] » page 67** est conservée. Elle n'est pas interprétée
comme preuve de droits compatibles. Le checksum du PDF reste **inconnu**.

Le corpus contient 207 faits, 237 critères (207 de recueil et 30 questions
médecin-médecin sans règles de preuve), 47 entrées de terminologie et 42 questions
ouvertes. Les 237 poids à 1 restent provisoires. Une ouverture allemande est absente.
`GLOBAL-Q004` n'a pas de page source : absence conservée et signalée explicitement.
Les null, négations explicites, inconnus, ambiguïtés et contradictions sont conservés.

Base locale **dédiée**, sans toucher à `var/ari.db` : `var/freiburg-review.db`.
Tables vérifiées après import : 1 lot, 10 brouillons; 0 cas exécutables, 0 scénario,
0 revue, 0 événement de publication et 0 session dans cette base.

Rapports privés générés et ignorés par Git :

- `private-case-sources/freiburg/rapport-import.json` ;
- `private-case-sources/freiburg/revue-complete.json` ;
- `private-case-sources/freiburg/revue-complete.md`.

La base et les rapports sont limités en lecture/écriture au propriétaire local.
Les exports originaux restent à leur emplacement. Aucun export ni contenu clinique
réel n'est ajouté à Git; les fixtures de tests sont exclusivement synthétiques.

## C. Validation humaine restante

Le dossier de revue est préparé pour les 10 cas; aucune personne n'a été présentée
comme ayant commencé ou terminé cette revue. Un humain doit vérifier clinique,
langue, données identifiantes, droits et questions ouvertes. Il faudra ensuite
préparer un scénario/rubrique explicite et une nouvelle version du bundle ARI,
puis effectuer ses revues sur les hashes exacts. L'import technique ne remplace
aucune de ces décisions. La publication n'est pas autorisée dans cette reprise.

**Goal 5 non lancé.** Aucun push, déploiement, appel IA payant, changement de modèle
vocal ou modification de la base applicative existante.

## Vérifications

- Suite complète finale : **183 tests passés**, PostgreSQL réel inclus, aucun skip.
  Un avertissement existant de dépréciation Starlette/httpx, sans échec.
- PostgreSQL 17.11 : nouvelle migration, JSONB, FKs et immutabilité des brouillons
  réellement vérifiés sur le cluster temporaire local, ainsi que le workflow historique.
- 21 nouveaux tests ciblés de réception, validation, mapping et intégrité : verts.
- Ruff et Mypy strict : verts (69 fichiers source).
- Frontend : syntaxe JavaScript, PCM, livraison audio et états de session : verts.
- Benchmark Goal 3 : smoke offline uniquement, vert.
- SQLite : migration explicite vers 0005 et `alembic check` sans drift; import réel
  précédé de validation seule et dry-run; transaction et réimport contrôlés.
- Revue indépendante : deux points corrigés (documentation obsolète et page source
  manquante non signalée); nouvelle vérification sans finding important restant.
- CI GitHub non exécutée; aucun push.
