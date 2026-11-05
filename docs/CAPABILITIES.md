# Capacités réelles d'ARI

État relevé le 15 octobre 2026 sur `main` (`87fa603`). Ce document dit ce que le produit sait faire
aujourd'hui, avec quel évaluateur, quels modèles et quel contenu, pour que la promesse commerciale ne
dépasse pas la capacité. À mettre à jour à chaque action du plan qui change une ligne.

Les modèles indiqués sont les **valeurs par défaut** de `backend/src/ari/config.py`. Depuis le
15 octobre 2026, les trois modèles texte (patient, évaluation, contenu) sont `gpt-6-luna`. Les valeurs réelles
de production viennent de l'environnement (`ARI_*`) et peuvent différer.

## Matrice par format

| Format | Implémenté | Évaluateur | Modèles configurés | Contenu publié disponible | Limites connues |
|---|---|---|---|---|---|
| Anamnèse, mode entraînement (push-to-talk) | Oui | Mixte. Déterministe pour les critères (`assessment-weighted-v1`), la structure (`anamnesis-sections-v1`) et les prochaines actions (`next-actions-v1`). LLM structuré pour le résumé, les erreurs de langue, l'empathie, le code-switching et les candidats lexicaux (`evaluation-v10`, schéma `session-evaluation-v5`) ; l'application possède tous les critères et scores numériques | STT `whisper-large-v3` (Groq), patient `gpt-6-luna` (`patient-v3`, ne choisit que des faits), évaluation `gpt-6-luna`, TTS `gpt-4o-mini-tts` voix `alloy` | Un cas synthétique français `ARI-DEV-FR@4`, scénario `arzt_patient` v4, CEFR B2, revues simulées. Aucun cas allemand validé | Pas de prononciation. État vocal en mémoire, un seul worker. Les erreurs de transcription peuvent être imputées à l'apprenant |
| Anamnèse, mode examen (micro ouvert) | Oui | Même évaluation finale, plus un audit LLM qui rattache chaque phrase du patient aux faits (`fact-attribution-v1`) | Temps réel `gpt-realtime-mini` voix `marin`, transcription `gpt-4o-mini-transcribe`, audit `gpt-6-luna`, VAD 900 ms | Le même cas synthétique | Le modèle parle librement, le contrôle est fait après coup. Quota de 2 examens par mois, fin à la durée du scénario plus 5 minutes, fermeture après 5 minutes sans activité. Un tour dans une autre langue n'est pas marqué `wrong_language` |
| Anamnèse, mode examen en chaîne (`ARI_EXAM_ENGINE=chain`, désactivé par défaut) | Oui, non confronté au service ElevenLabs réel | Même évaluation finale ; faits crédités après accusé de lecture, comme en entraînement ; rapport `exam-report-v1` | Whisper, `gpt-6-luna` avec `patient-exam-v1`, ElevenLabs `eleven_v4_turbo` (voix, réglages et balises par persona) | Le même cas synthétique | Phrases cliniques authored ; formulation libre seulement pour `small_talk`, `clarification_request`, `hesitation`, dite seulement si chaque mot est sur une liste fermée de mots de conversation, sinon phrase neutre fixe. Détection de parole par énergie côté serveur (seuils à régler sur appels réels). L'interruption coupe l'envoi et ne crédite rien |
| Arzt-Arzt | Oui, à l'écrit | Déterministe : réponse exacte normalisée (`practice-exact-answer-v1`, `practice-feedback-v1`) | Aucun | Scénario `arzt_arzt` du cas `ARI-DEV-FR` | Écrit alors que l'épreuve est orale. Une formulation non prévue est « non reconnue », pas jugée |
| Fachbegriffe | Oui | Déterministe, même méthode | Aucun | Scénario `fachbegriffe` du cas `ARI-DEV-FR` | Correspondance exacte, variantes rédigées à la main |
| Arztbrief | **Non** | Aucun | Aucun | Aucun | La phase `arztbrief` est réservée dans le registre mais non exécutable, et le générateur ne la produit jamais (`content/services/bundle_generator.py::available_phases`) |
| Test de niveau | Oui | Escalier déterministe (`placement-staircase-v2`, B1 à C1 avec sonde A2, 12 questions au plus, environ 10 minutes) pour le QCM et la compréhension orale. LLM structuré pour l'oral (`placement-speaking-v3`, `SpeakingRatingSchema` : cinq sous-notes de 1 à 6 sur l'échelle du CECRL, le niveau est lu par le code sur une grille fixe à partir de leur moyenne sur les passes, seuil de confiance 0,6) ; ses erreurs citées sont vérifiées par le code (`verify_language_errors`) et alimentent des points faibles par catégorie calculés par code | STT, TTS et `gpt-6-luna` (route évaluation, avec son effort de raisonnement, sa graine et ses `ARI_EVALUATION_PASSES` passes : une erreur est gardée si au moins une passe la voit (citation vérifiée par le code), le niveau vient de la moyenne des sous-notes de toutes les passes) | Jeu `de-DE` `cases/placement/ari_placement_de.v2.yaml` (relu par un juge LLM, grille `placement-grid-v1`, **à importer, relire et publier sur le serveur**), `fr-FR` (`cases/dev/`, développement), `de-DE` v1 (`cases/demo/`, tests) | Estimation CEFR non certifiée, jeu écrit par l'agent et relu par un juge LLM (pas par un germanophone). Niveau final = compétence la plus faible. Mesure sur les vrais modèles : suite `placement@1`, première mesure le 5 novembre 2026 (run 42023493910) : qualité bonne (précision 1,0, rappel 0,95) mais stabilité 0,40 pour 0,10, inchangée par le vote (0,37) puis par « une passe suffit » (0,40) ; suite `placement@2` (`gates-v13`) : stabilité mesurée sur le niveau, les points faibles et chaque erreur, holdout neuf de 60 transcripts, mesure dev du 5 novembre 2026 (run 42068460081) : erreurs 0,036 et points faibles 0,022 passent, niveau 0,133 pour 0,10, d'où la notation du niveau par sous-notes (`placement-speaking-v3`) ; mesure dev de v3 (run 42085869644) : niveau plus juste (exact 0,70) mais qui bascule sur les seuils (0,24), d'où `gates-v14` : seuil sur l'écart type du score de niveau entre répétitions (≤ 0,15 niveau), holdout `@2` pas encore lancé (`docs/EVALS.md`, section 11) |
| Carnet de vocabulaire | Oui | Répétition espacée déterministe (`srs-sm2-v1`). Le LLM propose seulement des candidats | Aucun en propre | Alimenté par les sessions et la terminologie des cas | Promotion lexicale (préfixe du mot), pas un jugement d'usage correct |

## Fonctions transverses

| Fonction | Méthode | Version | Stocké ? |
|---|---|---|---|
| Modèle de l'apprenant | Comptages déterministes sur les 5 dernières sessions, dont le profil de langue (catégories d'erreurs, axe du prochain exercice) | `learner-model-v2` | Non, recalculé à chaque lecture |
| Programme hebdomadaire | Règles (phases, créneaux, budget temps, score de recommandation, créneaux de remédiation sur une catégorie d'erreur de langue) | `program-rules-v4` | Non |
| Remédiation « Votre phrase » | Rejoue les erreurs de langue citées par l'évaluation (citation et correction vérifiées par le code) ; compare la réponse à la correction, un écart n'est jamais un échec | `language-drill-v1` | Oui, `language_drill_runs` (phrases figées à la création, réponses ajoutées) |
| Progression par axes | Déterministe, aucun score global | `progress-axes-v1` | Non |
| Métriques de session | Déterministe | `session-metrics-v1` | Oui |
| Évaluation des sorties IA (`ari.evals`) | Suites rejouées sur les services réels (patient d'entraînement et d'examen, évaluateur, chaîne vocale complète avec apprenant simulé), scorers déterministes, seuils `gates.yaml`, classement par coût | `evals-report-v1`, `gates-v7` | Rapports JSON hors base (`var/evals/`) ; voir `docs/EVALS.md` |

Il n'existe aujourd'hui ni compétence explicite, ni maîtrise persistée en dehors du carnet, ni estimation
d'incertitude (plan, actions 6 à 9).

## Contenu

- La seule source de contenu est le registre : import, deux revues humaines, puis publication.
- Le pipeline back-office fonctionne de bout en bout, de l'upload du PDF à la publication. Les versions utilisées sont :
  - `fsp-protocol-v1` et `clinical-case-v3` pour les schémas ;
  - `segmentation-v1`, `protocol-extraction-v2` et `bundle-draft-v1` pour les prompts ;
  - `pii-detector-v1` pour la détection de données personnelles.
- Aucun protocole réel n'est encore publié pour les apprenants. Tout le contenu livré est synthétique.

## Versions de référence

| Type | Identifiants actifs |
|---|---|
| Prompts apprenant (`backend/src/ari/prompts/`) | `patient-v3`, `patient-realtime-v1`, `evaluation-v10`, `fact-attribution-v1`, `placement-speaking-v3`. `placement_speaking_v1.txt`, `placement_speaking_v2.txt`, `patient_v2.txt`, `evaluation_v2.txt`, `evaluation_v6.txt` et `evaluation_v7.txt` sont présents mais non chargés. Les erreurs de langue de `evaluation-v10` portent une citation et une correction que le code vérifie (`language_errors.py`) |
| Prompts contenu (`backend/src/ari/prompts/content/`) | `segmentation-v1`, `protocol-extraction-v2`, `bundle-draft-v1`. `protocol_extraction_v1.txt` est présent mais non chargé |
| Schémas | `ari-clinical-bundle-v1`, `clinical-case-v3`, `practice-spec-v1`, `ari-placement-bundle-v1`, `placement-set-v1` et `placement-set-v2`, `fsp-protocol-v1`, `session-evaluation-v5` (lecture v2 à v4 conservée) |
| Scorers et règles | `assessment-weighted-v1`, `practice-exact-answer-v1`, `practice-feedback-v1`, `anamnesis-sections-v1`, `next-actions-v1`, `srs-sm2-v1`, `placement-staircase-v1`, `learner-model-v2`, `program-rules-v4`, `language-drill-v1`, `progress-axes-v1`, `session-metrics-v1` |
| Rubriques | `fsp-anamnesis@1`, `fsp-arzt-arzt@1`, `fsp-fachbegriffe@1` (générées par `content/services/rubrics.py`) |
| Piles vocales | `pipeline_economy` v1 (entraînement), `realtime_exam` v1 (examen), `chain_exam` v1 (examen en chaîne) |

## Validation au moment du relevé

| Commande | Résultat |
|---|---|
| `make lint` (ruff, mypy strict sur 152 fichiers, import-linter) | Vert, 4 contrats respectés |
| `make test-web` | Vert |
| `make test-backoffice-web` | Vert |
| `make test-backend` | Vert, 237 tests (PostgreSQL 16 local) |
