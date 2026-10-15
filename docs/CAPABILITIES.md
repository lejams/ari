# Capacités réelles d'ARI

État relevé le 15 octobre 2026 sur `main` (`87fa603`). Ce document dit ce que le produit sait faire
aujourd'hui, avec quel évaluateur, quels modèles et quel contenu, pour que la promesse commerciale ne
dépasse pas la capacité. À mettre à jour à chaque action du plan qui change une ligne.

Les modèles indiqués sont les **valeurs par défaut** de `backend/src/ari/config.py`. Les valeurs réelles
de production viennent de l'environnement (`ARI_*`) et peuvent différer.

## Matrice par format

| Format | Implémenté | Évaluateur | Modèles configurés | Contenu publié disponible | Limites connues |
|---|---|---|---|---|---|
| Anamnèse, mode entraînement (push-to-talk) | Oui | Mixte. Déterministe pour les critères (`assessment-weighted-v1`), la structure (`anamnesis-sections-v1`) et les prochaines actions (`next-actions-v1`). LLM structuré pour le résumé, les erreurs de langue, l'empathie, le code-switching et les candidats lexicaux (`evaluation-v6`, schéma `session-evaluation-v4`) ; les scores par critère du LLM sont validés puis ignorés | STT `whisper-large-v3` (Groq), patient `gpt-5.6-luna` (`patient-v3`, ne choisit que des faits), évaluation `gpt-5.6-terra`, TTS `gpt-4o-mini-tts` voix `alloy` | Un cas synthétique français `ARI-DEV-FR@4`, scénario `arzt_patient` v4, CEFR B2, revues simulées. Aucun cas allemand validé | Pas de prononciation. État vocal en mémoire, un seul worker. Les erreurs de transcription peuvent être imputées à l'apprenant |
| Anamnèse, mode examen (micro ouvert) | Oui | Même évaluation finale, plus un audit LLM qui rattache chaque phrase du patient aux faits (`fact-attribution-v1`) | Temps réel `gpt-realtime-mini` voix `marin`, transcription `gpt-4o-mini-transcribe`, audit `gpt-5.6-luna`, VAD 900 ms | Le même cas synthétique | Le modèle parle librement, le contrôle est fait après coup. Aucune durée maximale ni aucun quota. Un tour dans une autre langue n'est pas marqué `wrong_language` |
| Arzt-Arzt | Oui, à l'écrit | Déterministe : réponse exacte normalisée (`practice-exact-answer-v1`, `practice-feedback-v1`) | Aucun | Scénario `arzt_arzt` du cas `ARI-DEV-FR` | Écrit alors que l'épreuve est orale. Une formulation non prévue est « non reconnue », pas jugée |
| Fachbegriffe | Oui | Déterministe, même méthode | Aucun | Scénario `fachbegriffe` du cas `ARI-DEV-FR` | Correspondance exacte, variantes rédigées à la main |
| Arztbrief | **Non** | Aucun | Aucun | Aucun | La phase `arztbrief` est réservée dans le registre mais non exécutable, et le générateur ne la produit jamais (`content/services/bundle_generator.py::available_phases`) |
| Test de niveau | Oui | Escalier déterministe (`placement-staircase-v1`) pour le QCM et la compréhension orale. LLM structuré pour l'oral (`placement-speaking-v1`, `SpeakingRatingSchema`, seuil de confiance 0,6) | STT, TTS et `gpt-5.6-luna` | Jeu `fr-FR` (`cases/dev/ari_placement_dev.v1.yaml`, développement) et `de-DE` (`cases/demo/ari_placement_demo.v1.yaml`, tests) | Estimation CEFR non certifiée. Niveau final = compétence la plus faible |
| Carnet de vocabulaire | Oui | Répétition espacée déterministe (`srs-sm2-v1`). Le LLM propose seulement des candidats | Aucun en propre | Alimenté par les sessions et la terminologie des cas | Promotion lexicale (préfixe du mot), pas un jugement d'usage correct |

## Fonctions transverses

| Fonction | Méthode | Version | Stocké ? |
|---|---|---|---|
| Modèle de l'apprenant | Comptages déterministes sur les 5 dernières sessions | `learner-model-v1` | Non, recalculé à chaque lecture |
| Programme hebdomadaire | Règles (phases, créneaux, budget temps, score de recommandation) | `program-rules-v2` | Non |
| Progression par axes | Déterministe, aucun score global | `progress-axes-v1` | Non |
| Métriques de session | Déterministe | `session-metrics-v1` | Oui |

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
| Prompts apprenant (`backend/src/ari/prompts/`) | `patient-v3`, `patient-realtime-v1`, `evaluation-v6`, `fact-attribution-v1`, `placement-speaking-v1`. `patient_v2.txt` et `evaluation_v2.txt` sont présents mais non chargés |
| Prompts contenu (`backend/src/ari/prompts/content/`) | `segmentation-v1`, `protocol-extraction-v2`, `bundle-draft-v1`. `protocol_extraction_v1.txt` est présent mais non chargé |
| Schémas | `ari-clinical-bundle-v1`, `clinical-case-v3`, `practice-spec-v1`, `ari-placement-bundle-v1`, `placement-set-v1`, `fsp-protocol-v1`, `session-evaluation-v4` (lecture v2 et v3 conservée) |
| Scorers et règles | `assessment-weighted-v1`, `practice-exact-answer-v1`, `practice-feedback-v1`, `anamnesis-sections-v1`, `next-actions-v1`, `srs-sm2-v1`, `placement-staircase-v1`, `learner-model-v1`, `program-rules-v2`, `progress-axes-v1`, `session-metrics-v1` |
| Rubriques | `fsp-anamnesis@1`, `fsp-arzt-arzt@1`, `fsp-fachbegriffe@1` (générées par `content/services/rubrics.py`) |
| Piles vocales | `pipeline_economy` v1 (entraînement), `realtime_exam` v1 (examen) |

## Validation au moment du relevé

| Commande | Résultat |
|---|---|
| `make lint` (ruff, mypy strict sur 152 fichiers, import-linter) | Vert, 4 contrats respectés |
| `make test-web` | Vert |
| `make test-backoffice-web` | Vert |
| `make test-backend` | Vert, 237 tests (PostgreSQL 16 local) |
