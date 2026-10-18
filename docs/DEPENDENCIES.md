# Dépendances externes et dépréciations

Relevé du 15 octobre 2026. À vérifier à nouveau sur la
[page des dépréciations OpenAI](https://developers.openai.com/api/docs/deprecations) avant chaque
décision de calendrier : les dates ci-dessous viennent de cette page quand c'est indiqué, sinon de
sources secondaires signalées comme telles.

## Modèles d'IA

Les modèles indiqués sont les valeurs par défaut de `backend/src/ari/config.py`. La production peut les
remplacer par des variables `ARI_*`. Les valeurs réellement déployées sont à confirmer par le
propriétaire, car les fichiers `.env` ne sont pas lisibles par les outils.

| Usage dans ARI | Réglage | Défaut | Statut annoncé | Source | Remplacement prévu |
|---|---|---|---|---|---|
| TTS du mode entraînement (`pipeline_economy`) et du test de niveau | `ARI_TTS_MODEL` | `gpt-4o-mini-tts` | **Retrait le 6 janvier 2027.** OpenAI recommande `gpt-realtime-2.1-mini`, qui ne serait pas disponible sur l'endpoint `/v1/audio/speech` | Page des dépréciations OpenAI ; [fil de la communauté OpenAI](https://community.openai.com/t/tts-shutdown-email-tts-1-gpt-4o-mini-tts-jan-6-2027-isnt-on-the-deprecations-page/1402731) ; [analyse MIXED](https://mixed-news.com/en/openai-tts-replacement-gpt-realtime-2-1-mini-audio-speech-not-supported/) | ElevenLabs derrière `StreamingTTSProvider` (actions 3 et 11) |
| Patient temps réel du mode examen (`realtime_exam`) | `ARI_REALTIME_MODEL` | `gpt-realtime-mini` | **Retrait le 20 janvier 2027**, remplacement `gpt-realtime-2.1-mini`. Les pages modèles indiquent déjà « Deprecated » | Page des dépréciations OpenAI, entrée du 20 juillet 2026 | Chaîne STT → LLM → ElevenLabs (action 3). OpenAI Realtime reste un repli derrière le port |
| Transcription du mode examen | `ARI_REALTIME_TRANSCRIPTION_MODEL` | `gpt-4o-mini-transcribe` | **Retrait le 26 février 2027** (le snapshot `gpt-4o-mini-transcribe-2025-03-20` figurerait au 20 janvier 2027). Remplacements : `gpt-live-transcribe` ou `gpt-transcribe` | Page des dépréciations OpenAI | Disparaît avec la chaîne de l'action 3 (STT de la chaîne) |
| STT du mode entraînement et du test de niveau | `ARI_STT_BASE_URL`, `ARI_STT_MODEL` | Groq, `whisper-large-v3` | Aucun retrait connu chez Groq. Si la production utilise OpenAI `whisper-1`, son retrait est annoncé au **26 février 2027** par des sources secondaires, non confirmé sur la page officielle | [kingy.ai](https://kingy.ai/ai-launch-tracker/openai-transcription-models-api-shutdown-february-26-2027/), [layer3labs](https://www.layer3labs.io/guides/openai-deprecated-models) | Rester sur Groq ou passer à `gpt-transcribe`. Comparaison avec ElevenLabs Scribe au spike 3a |
| LLM patient, audit, oral du test de niveau | `ARI_PATIENT_MODEL` | `gpt-6-luna` (avant : `gpt-5.6-luna`) | Aucun retrait connu | n/a | Décision du propriétaire du 15 octobre 2026 : tous les modèles texte sur `gpt-6-luna` |
| LLM évaluation et pipeline de contenu | `ARI_EVALUATION_MODEL`, `ARI_CONTENT_MODEL` | `gpt-6-luna` (avant : `gpt-5.6-terra`) | Aucun retrait connu | n/a | Idem, sans mesure préalable par `ari.evals` (voir conséquence 4) |

## Conséquences sur le plan

1. **La TTS du mode entraînement est le premier retrait, avant celui du temps réel.** Le 6 janvier 2027,
   le mode entraînement et l'oral du test de niveau perdent leur voix si rien ne change. Le plan prévoit
   l'adaptateur ElevenLabs à l'action 3 (examen) et la pré-génération à l'action 11 (phase 2a, après
   février). **Décision du propriétaire (15 octobre 2026) : tester ElevenLabs sur la pile d'entraînement
   et la basculer avant le 6 janvier 2027.** Le spike 3a doit donc couvrir l'entraînement autant que
   l'examen.
2. **Le jalon J2 n'est pas contraint par le retrait du temps réel.** La production utilise déjà
   `ARI_REALTIME_MODEL=gpt-realtime-2.1-mini` (confirmé par le propriétaire le 15 octobre 2026), absent
   de la liste de retrait. La date du 20 janvier 2027 ne concerne que le défaut du code. La transcription
   temps réel (`gpt-4o-mini-transcribe`) reste concernée : retrait le 26 février 2027.
3. Mettre à jour les valeurs par défaut de `config.py` et de `docker-compose.yml` dès que les
   remplacements sont choisis, pour qu'un environnement neuf ne démarre pas sur un modèle retiré.
4. **Passage à `gpt-6-luna`.** Le modèle est léger et environ 20 fois moins cher que `gpt-5.6-terra`
   (voir tarifs). Le plan exige un rapport `ari.evals` sans nouvelle erreur critique avant tout changement
   de modèle en production (invariant 5). Or ce cadre n'existe pas encore (action 8). L'évaluation de
   session et l'extraction de protocoles sont les usages les plus sensibles à la qualité du modèle.
   Le propriétaire a choisi de tout basculer sans mesure préalable : le rapport `ari.evals` de
   l'action 8 servira de contrôle a posteriori, avec retour sur `gpt-5.6-terra` par simple
   variable d'environnement si une régression apparaît.

## Tarifs publics relevés (P0.2)

Relevé du 15 octobre 2026, prix catalogue en dollars hors taxes. Ces valeurs alimenteront
`pricing/pricing-v1.yaml` à l'action 1.
- Les remises propres au compte ne sont pas connues.
- Les montants marqués « secondaire » viennent de sites tiers ; ils sont à confirmer sur la page
  officielle du fournisseur avant de figer la grille.
- La conversion en euros est un paramètre de la grille. Valeur de travail : 1 $ = 0,88 €, pour un
  EUR/USD autour de 1,12 à 1,15 en 2026, cours d'octobre non vérifié.

| Fournisseur, modèle | Unité | Prix | Fiabilité | Source |
|---|---|---|---|---|
| OpenAI `gpt-5.6-terra` | 1 M jetons d'entrée / entrée en cache / sortie | 2,00 $ / 0,20 $ / 12,00 $ (après la baisse du 30 juillet 2026 ; batch 1,00 $ / 6,00 $) | Secondaire, concordant | [morphllm](https://www.morphllm.com/openai-api-pricing), [pricepertoken](https://pricepertoken.com/pricing-page/model/openai-gpt-5.6-terra), [techjacksolutions](https://techjacksolutions.com/ai-tools/chatgpt/gpt-5-6-pricing/) |
| OpenAI `gpt-6-luna` (sorti le 22 septembre 2026) | 1 M jetons d'entrée / sortie | 0,10 $ / 0,50 $ (une source divergente à 0,30 $) ; prix en cache non trouvé | Secondaire | [OpenRouter](https://openrouter.ai/models/openai/gpt-6-luna), [LLM Reference](https://www.llmreference.com/model/gpt-6-luna/openai-api), [Let's Data Science](https://letsdatascience.com/news/openai-expands-gpt-56-options-in-chatgpt-360ebb75) |
| OpenAI `gpt-realtime-2.1-mini`, audio | 1 M jetons d'entrée / entrée en cache / sortie | 10,00 $ / 0,30 $ / 20,00 $ | Officiel | [Tarifs OpenAI](https://developers.openai.com/api/docs/pricing), [page modèle](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini) |
| OpenAI `gpt-realtime-2.1-mini`, texte | 1 M jetons d'entrée / entrée en cache / sortie | 0,60 $ / 0,06 $ / 2,40 $ | Secondaire | [glamdringresearch](https://www.glamdringresearch.com/post/openai-realtime-api-pricing) |
| OpenAI `gpt-4o-mini-transcribe` | minute | environ 0,003 $ (estimation ; la facturation réelle se fait en jetons, 1,25 $ / 5,00 $ par M) | Secondaire | [diyai](https://diyai.io/ai-tools/speech-to-text/openai-whisper-api-pricing-2026/), [OpenRouter](https://openrouter.ai/models/openai/gpt-4o-mini-transcribe) |
| OpenAI `gpt-4o-mini-tts` | minute d'audio | environ 0,015 $ (12,00 $ par M jetons audio de sortie) | Faible (copie non datée de la page OpenAI) | [Scribd](https://www.scribd.com/document/859642842/Pricing-OpenAI-API) |
| Groq `whisper-large-v3` | heure d'audio | 0,111 $, minimum facturé de 10 s par requête | Officiel | [Groq](https://console.groq.com/docs/model/whisper-large-v3) |
| ElevenLabs `eleven_v4_turbo` | 1 000 caractères | 0,04 $ (remise de lancement à 0,011 $ terminée le 12 octobre 2026) | Secondaire, concordant | [eesel](https://www.eesel.ai/blog/eleven-v4-pricing), [ElevenLabs sur X](https://x.com/ElevenLabs/status/2104572138347004161) |
| ElevenLabs `eleven_v4` | 1 000 caractères | 0,08 $ | Secondaire, concordant | idem |
| ElevenLabs Scribe v2 (batch / temps réel) | heure d'audio | 0,22 $ (0,27 $ selon une source) / 0,39 $ | Secondaire, divergent | [Puter](https://developer.puter.com/tutorials/elevenlabs-api-pricing/), [Soniox](https://soniox.com/compare/stt/pricing) |

### Coûts par session recalculés avec ces tarifs

Ce sont des estimations, pas des mesures ; l'action 1 donnera les coûts réels.

Hypothèses :
- session de 20 minutes, 25 à 30 tours ;
- 8 à 12 minutes de parole de l'apprenant et 4 à 6 minutes de parole du patient ;
- environ 900 caractères par minute d'allemand parlé ;
- évaluation finale à 15 000 jetons en entrée et 3 000 en sortie sur `gpt-5.6-terra` ;
- les éventuels jetons de raisonnement de `gpt-5.6-terra` et le débit réel de jetons audio du temps réel
  ne sont pas connus.

| Session | Détail | Coût estimé |
|---|---|---|
| Entraînement, état actuel avec `gpt-5.6-terra` pour le patient | STT 0,015 $ + patient 0,18 $ + TTS OpenAI 0,06 $ + évaluation 0,07 $ | environ 0,32 $, soit 0,28 € |
| Entraînement, TTS ElevenLabs `eleven_v4_turbo` en streaming, sans cache | STT 0,015 $ + patient 0,18 $ + TTS 0,14 $ + évaluation 0,07 $ | environ 0,41 $, soit 0,36 € |
| Entraînement, phrases pré-générées (`eleven_v4`) et cache de prompt | STT 0,015 $ + patient 0,07 $ + TTS mutualisé + évaluation 0,07 $ | environ 0,15 $, soit 0,13 € |
| Examen, `gpt-realtime-2.1-mini` (production actuelle) | audio 0,30 à 0,60 $ + audit `fact-attribution-v1` sur terra environ 0,13 $ + évaluation 0,07 $ | environ 0,50 à 0,80 $, soit 0,44 à 0,70 € |
| Examen, chaîne de l'action 3 (STT + terra + `eleven_v4_turbo`) | STT 0,02 $ + patient 0,08 à 0,22 $ + TTS 0,03 à 0,22 $ (selon la part de phrases pré-générées) + évaluation 0,07 $ | environ 0,20 à 0,53 $, soit 0,18 à 0,46 € |

À retenir :
- La TTS ElevenLabs en streaming coûte plus cher par session que la TTS OpenAI actuelle. Elle ne
  devient avantageuse que si les phrases sont pré-générées et partagées entre apprenants, ce que permet
  la règle « phrases cliniques rédigées et validées ».
- Sur ces estimations, le temps réel `gpt-realtime-2.1-mini` tient déjà la cible de 0,70 € par
  examen. L'action 3 se justifie donc surtout par le contrôle clinique (phrases validées, pas de
  formulation libre) plutôt que par le coût. C'est à confirmer par la mesure.
- Ce tableau suppose `gpt-5.6-terra`. Avec `gpt-6-luna` aux prix relevés, le patient tombe à environ
  0,01 $ par session et l'évaluation à environ 0,003 $. Une session d'entraînement coûterait alors
  environ 0,09 $ (0,08 €). L'essentiel du coût restant vient de la TTS et du STT.

## Autres dépendances externes

| Dépendance | Rôle | Remarque |
|---|---|---|
| OpenAI | LLM, TTS, temps réel | Plafond mensuel fixé dans le tableau de bord. Pas de quota par utilisateur dans ARI (action 2) |
| Groq | STT | Point de terminaison compatible OpenAI |
| ElevenLabs | TTS cible (action 3) | Pas encore intégré. Modèles retenus : `eleven_v4_turbo` (streaming examen) et `eleven_v4` (pré-génération) |
| Serveur SMTP | E-mails de compte | `ARI_EMAIL_MODE=log` en développement |
| Hébergeur VPS, stockage S3 compatible | Exploitation, sauvegardes chiffrées | Voir `docs/DEPLOYMENT.md` |
