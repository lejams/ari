# Télémétrie et benchmark vocal

## Mesures persistées

Chaque tour vocal peut conserver une métrique `voice-turn-metric-v2`, corrélée à la
session, au tour, au trace ID, à la stack et sa version, aux modèles, au cas, au mode
d’interaction, aux prompts, aux identifiants fournisseur et à la version applicative.
Les timestamps muraux UTC servent uniquement au diagnostic.

Une durée est calculée avec une horloge monotone dans un seul domaine (`server`,
`browser` ou `provider`). Deux marques de domaines différents ne sont jamais
soustraites. Le navigateur envoie des durées observées, et non une heure absolue
supposée synchronisée avec le serveur. Une observation absente reste `null`, jamais
zéro. La latence avant premier audio envoyé et celle avant lecture réellement observée
restent donc deux mesures distinctes.

Les durées versionnées sont :

- `speech_end_to_transcript_final_ms` ;
- `transcript_final_to_llm_first_token_ms` ;
- `llm_total_ms` ;
- `llm_complete_to_tts_first_byte_ms` ;
- `tts_total_ms` ;
- `speech_end_to_first_audio_sent_ms` ;
- `speech_end_to_audio_started_ms` ;
- `audio_sent_to_playback_started_ms` ;
- `turn_total_ms`.

Les compteurs d’erreur, retry et interruption, ainsi que le statut de livraison,
sont conservés séparément. `delivery_status=delivered` signifie que la lecture complète
a été confirmée ; `unconfirmed` (état de tour `delivery_unconfirmed`) ne lui est jamais
assimilé.

Les champs décrivent uniquement les étapes réellement observables : le premier token
LLM reste `null` pour l’appel structuré non streamé actuel. Realtime ne fournit pas
d’étape LLM isolée, donc `llm_total_ms` y reste `null`. `turn_total_ms` mesure la fin
du traitement serveur (fin de réponse Realtime ou fin d’envoi pipeline), pas la fin
acoustique de lecture. La confirmation de lecture reste un signal distinct.

## Agrégation technique

La commande suivante lit uniquement les métriques de la base et produit un JSON :

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.telemetry.cli \
  --database-url sqlite:///var/ari.db \
  --window-start 2026-09-01T00:00:00+00:00 \
  --window-end 2026-10-01T00:00:00+00:00
```

Les groupes séparent la stack, sa version, les modèles, le transport, le mode
d’interaction et la fenêtre. Les percentiles utilisent exactement la méthode
nearest-rank : valeurs triées, rang `ceil(percentile × n)`, avec rang minimal 1.
Les valeurs manquantes ne participent pas au percentile, mais leur nombre est
rapporté. Moins de 20 valeurs disponibles donne `insufficient_data`.

`GET /api/technical/voice-metrics` expose le même agrégat sans transcript, contenu
clinique, learner ID, session ID ni turn ID. La route répond `404` lorsque
`ARI_ENVIRONMENT=production`, en attendant une authentification technique.

## Benchmark hors ligne

Le smoke benchmark utilise un corpus allemand/français inspectable et entièrement
synthétique. Il vérifie le calcul des métriques et la génération des rapports ; il ne
prouve ni la qualité d’un accent humain, ni la naturalité, ni l’empathie.

```bash
PYTHONPATH=backend/src .venv/bin/python -m ari.benchmarks.voice \
  --suite benchmarks/voice/german_medical_smoke.v1.yaml \
  --mode offline \
  --stack pipeline_economy \
  --output-dir benchmark-results/
```

`--stack` est répétable. Sans cette option, les quatre fixtures de stack sont
exécutées. Les autres options sont `--repetitions`, `--seed`, `--scenario`,
`--rules`, `--max-cost-usd` et `--confirm-live`. Le mode par défaut est `offline`.
`make benchmark-smoke` exécute les quatre stacks sans réseau et écrit uniquement
dans `/tmp/ari-benchmark-smoke`.

Chaque run produit :

- un rapport JSON machine-readable ;
- un rapport Markdown français ;
- un manifeste JSON contenant commit, versions, configuration, seed et répétitions.

La normalisation allemande applique Unicode NFC, casse uniforme et suppression de la
ponctuation tout en conservant nombres, unités et négations. L’équivalence entre mots
et chiffres est volontairement limitée à 0–12 ainsi qu’à `einmal`, `zweimal` et
`dreimal`. Le WER, le rappel terminologique, les entités critiques, les doses, les
nombres et les négations sont calculés sans juge externe. Les invariants patient sont
déterministes. Les mesures TTS locales couvrent PCM16, sample rate, durée, silence,
clipping et audio vide ; le round-trip ASR reste futur.

Les seuils champion/challenger sont versionnés dans
`benchmarks/voice/champion_challenger.v1.yaml`. Les fixtures synthétiques conservent
un coût et une qualité humaine inconnus : elles doivent donc conclure
`insufficient_data` et ne peuvent jamais promouvoir automatiquement une stack.

## Mode live et coût

Aucun appel live n’est effectué par les tests, la CI ou le smoke benchmark. Le CLI
refuse le mode live tant que l’ensemble des garde-fous n’est pas présent : mode
explicite, `ARI_BENCHMARK_LIVE_ENABLED=true`, clé fournisseur, plafond strictement
positif et `--confirm-live`. Le budget réserve un coût calculable avant chaque appel
et refuse une opération inconnue sans réservation conservatrice.

Le connecteur qui exécuterait réellement les quatre parcours live n’est pas encore
fourni : même après validation des garde-fous, le CLI refuse toute exécution réseau.
Le workflow manuel `benchmark-live.yml` prépare les entrées, l’environnement protégé
et le secret attendu pour une future exécution autorisée. Actuellement il utilise
`--validate-live-guard-only` : il valide la configuration puis termine sans appel ni
rapport de benchmark. L’exécution réelle nécessitera l’ajout et la revue de cet
adaptateur. Le workflow n’a aucun cron et ne contient aucune clé.

En pipeline, le timestamp du premier audio envoyé est pris après le premier envoi
WebSocket réussi. WebRTC contourne le serveur : cette métrique y reste `null`, et
seule l’observation navigateur mesure le début de lecture. Les mesures de lecture
WebAudio observent l’avancement du contexte audio au-delà de l’instant programmé ;
elles ne prétendent pas mesurer le son acoustique à la sortie du haut-parleur.

Un futur run live nécessite au minimum des audios allemands consentis, leurs
transcripts gold, les métadonnées d’accent/bruit/matériel, une revue germanophone,
une clé isolée, un environnement GitHub protégé, une autorisation explicite et un
plafond de coût approuvé.
