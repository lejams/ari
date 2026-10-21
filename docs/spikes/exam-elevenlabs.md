# Spike 3a : voix ElevenLabs `eleven_v4_turbo` pour le patient simulé

Statut : **décision prise le 20 octobre 2026 : GO pour 3b sans mesure préalable** (section 8).
Rédigé le 19 octobre 2026. Le protocole des sections 3 à 5 reste valable pour une mesure ultérieure.

## 1. Pourquoi ce spike

`gpt-4o-mini-tts` s'arrête le **6 janvier 2027** (voir `docs/DEPENDENCIES.md`). Il porte la voix du
mode entraînement et de l'oral du test de niveau. Le propriétaire a choisi ElevenLabs, modèle
`eleven_v4_turbo`. Avant d'écrire l'adaptateur de production, on mesure sur son réseau, avec ses
voix, si ce modèle tient le budget de latence, le coût et la qualité en allemand médical.

Le même adaptateur servira ensuite l'examen (action 3b) et la pré-génération (action 11).

## 2. Ce que la documentation dit (relevé du 19 octobre 2026, sources secondaires à confirmer)

| Point | Constat | Conséquence pour ARI |
|---|---|---|
| Point d'accès | `eleven_v4_turbo` n'est servi que par le **WebSocket Text to Dialogue** (`wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`). Le WebSocket TTS classique répond HTTP 400 pour ce modèle | L'adaptateur est un client WebSocket, pas un simple appel HTTP |
| Voix | **Une seule voix par connexion** pour `eleven_v4_turbo` | Une connexion par voix (par persona) : un petit pool, pas une connexion partagée |
| Début de parole | Le serveur retient le texte jusqu'à environ 40 caractères et 8 mots, sauf `flush` | Envoyer chaque phrase rédigée avec `flush: true`. Nos phrases sont complètes |
| Annulation | Pas de message pour arrêter une synthèse en cours | Le barge-in coupe la lecture côté navigateur et abandonne la connexion |
| Connexion inactive | Un `keep_alive` toutes les 10 s la maintient ouverte | À prévoir si on réutilise la connexion entre deux tours |
| Format audio | `pcm_24000` existe pour la TTS ; non confirmé sur le point d'accès dialogue. Les formats à 44,1 kHz demandent un plan Pro | **À vérifier en premier** avec `--debug-frames`. Le code attend du PCM 16 bits à 24 kHz |
| Allemand | Le modèle gère 90+ langues dont l'allemand | Qualité à écouter, en particulier les chiffres et les termes médicaux |
| Latence annoncée | environ 100 ms d'inférence, environ 150 ms jusqu'à la première parole, **hors réseau** | Chiffres du fournisseur : c'est ce que le spike mesure pour de vrai |
| Prix | 0,04 $ pour 1 000 caractères, déjà dans `pricing-v1.yaml` | Une session d'entraînement envoie environ 4 000 à 5 000 caractères |

Sources : [changelog du 28 septembre 2026](https://elevenlabs.io/docs/changelog/2026/9/28),
[WebSocket Text to Dialogue](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket),
[TTS contre TTD](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/tts-vs-ttd-websockets),
[LiveKit, routage v4 vers le dialogue](https://github.com/livekit/agents/pull/7516),
[Pipecat](https://github.com/pipecat-ai/pipecat/pull/5959),
[Eleven v4 expliqué](https://www.eesel.ai/blog/eleven-v4).

## 3. Protocole de mesure

### 3.1 Préparer

1. Choisir **2 à 4 voix** allemandes dans la bibliothèque ElevenLabs et noter leur `voice_id`
   (page Voices). Idéalement : une voix féminine et une masculine, calmes, et une plus âgée.
2. Mettre la clé dans le shell, **jamais dans la ligne de commande ni dans ce dépôt** :
   `export ELEVENLABS_API_KEY=...`
3. Pour comparer à la voix actuelle, ajouter `OPENAI_API_KEY` (la voix actuelle reste disponible
   jusqu'au 6 janvier).

### 3.2 Essai à blanc, sans clé

```sh
.venv/bin/python scripts/spikes/fake_elevenlabs_server.py &
ELEVENLABS_API_KEY=dummy .venv/bin/python scripts/spikes/elevenlabs_tts_bench.py \
  --voice demo=voice-1 --base-url ws://127.0.0.1:8765/v1/text-to-dialogue/stream-input
```

### 3.3 Premier essai réel (une phrase, pour voir le protocole)

```sh
.venv/bin/python scripts/spikes/elevenlabs_tts_bench.py --voice anna=<voice_id> \
  --repeats 1 --cold-limit 1 --debug-frames 6
```

Regarder les trames affichées. Si la connexion échoue ou si aucun audio n'arrive, le script
affiche la raison renvoyée par ElevenLabs et une piste (`--register-shape ids`, format audio).
**Ne pas lancer la mesure complète avant que cette étape passe.**

### 3.3bis Mesure complète

```sh
.venv/bin/python scripts/spikes/elevenlabs_tts_bench.py \
  --voice anna=<id> --voice paul=<id> --voice ... --baseline-openai --repeats 3
```

Depuis la **machine qui hébergera ARI** (le VPS) pour des chiffres réalistes. Depuis WSL, les
chiffres sont indicatifs : le réseau domestique ajoute son propre délai.

Le script écrit dans `var/spikes/elevenlabs/<date-heure>/` :
`results.json` (chaque mesure), `audio/` (un WAV par voix et phrase), `blind/` et `blind_key.json`.

### 3.4 Ce que la mesure ne couvre pas

- la latence **navigateur** (décodage, démarrage de la lecture) ;
- l'interruption (barge-in) et la reprise de la parole ;
- la voix de l'examen en conversation libre ;
- la qualité perçue, qui demande des humains (section 4).

## 4. Test d'écoute à l'aveugle

Le dossier `blind/` contient les mêmes phrases dites par chaque voix (ElevenLabs et OpenAI), sous des
noms anonymes `001.wav`, `002.wav`… dans un ordre aléatoire. `blind_key.json` indique qui est qui :
**ne pas le montrer aux écouteurs**.

5 personnes, idéalement germanophones ou médecins, notent chaque fichier de 1 à 5 :

| Fichier | Naturel | Clarté | Prononciation des termes médicaux et des nombres | Crédible comme patient |
|---|---|---|---|---|
| 001 | | | | |

Vérification obligatoire par une personne qui lit l'allemand, sur **chaque** fichier de la catégorie
`chiffres` et `medicaments` : « 38,5 Grad », « 150 zu 95 », « 50 Milligramm » doivent être
prononcés correctement. Une erreur sur un chiffre ou une dose est éliminatoire pour la voix.

## 5. Seuils d'acceptation (hypothèses, à confirmer par la mesure)

| Critère | Seuil |
|---|---|
| Premier audio, connexion déjà ouverte, P95 | ≤ 600 ms |
| Premier audio, connexion neuve (ouverture comprise), P95 | ≤ 1 500 ms |
| Temps total de synthèse par rapport à la durée de l'audio | < 1 (le flux va plus vite que la lecture) |
| Chiffres, unités et doses | aucune erreur de prononciation |
| Préférence à l'aveugle (5 personnes) | majorité ou égalité face à la voix OpenAI actuelle |
| Coût d'une session d'entraînement sans cache | ≤ 0,25 € de voix |

Budget global : fin de parole de l'apprenant jusqu'au début de la réponse du patient ≤ 2,5 s (P95),
4 s maximum. La voix ne doit pas consommer plus du quart de ce budget.

## 6. Ce que ça implique pour l'implémentation (action 3b)

- `infrastructure/providers/elevenlabs/tts.py` implémente `StreamingTTSProvider`
  (`application/ports/tts.py`) : le reste du code ne change pas.
- Une connexion par `(voice_id)`, ouverte à la demande, gardée vivante, rouverte après une
  erreur ; un seul message en vol par connexion (une phrase à la fois).
- `websockets` devient une dépendance directe de `backend/pyproject.toml` (aujourd'hui
  transitive).
- Coût enregistré par appel via l'action 1 : unité `tts_character`, modèle `eleven_v4_turbo`.
- Réglages : modèle, `voice_id` par persona, délai maximal, format de sortie.
- Cache des phrases rédigées par hash (texte, voix, modèle) : action 11 ; il rend la latence de
  connexion sans objet pour la plupart des tours.
- La pré-génération à la publication (action 11) utilisera plutôt `eleven_v4` en HTTP, plus fidèle.

## 7. Résultats

**Mesure non faite.** Le propriétaire a décidé le 20 octobre 2026 de passer à 3b sans mesure ni écoute à
l'aveugle (section 8). Le tableau ci-dessous reste vide ; les seuils de la section 5 seront vérifiés en
conditions réelles, sur le trafic de production (latence et coût de chaque appel sont enregistrés par
l'action 1).


| Voix | Mode | n | Erreurs | 1er audio P50 / P95 | Total P50 / P95 | x temps réel | € pour 1 000 car. |
|---|---|---|---|---|---|---|---|
| | cold | | | | | | |
| | warm | | | | | | |
| OpenAI (référence) | baseline | | | | | | |

Format audio effectivement obtenu : ______ . Écarts avec la documentation : ______ .

Notes d'écoute (moyenne sur 5 personnes) : ______ .

## 8. Décision (prise par le propriétaire le 20 octobre 2026)

- [x] **GO pour 3b sans mesure préalable.** Le propriétaire retient ElevenLabs `eleven_v4_turbo` pour
      son rapport qualité, latence et prix, sans exécuter la mesure ni l'écoute à l'aveugle de ce spike.
      Conditions qui en découlent :
      - le protocole WebSocket de l'adaptateur suit la documentation (section 2) **sans avoir été
        confronté au service réel** : le premier appel avec la vraie clé fait office d'essai. L'adaptateur
        est donc livré désactivé par défaut (`ARI_TTS_PROVIDER=openai`) et se bascule par variable
        d'environnement, avec retour immédiat sur OpenAI tant que `gpt-4o-mini-tts` existe
        (6 janvier 2027) ;
      - les voix (`voice_id`) restent à choisir par le propriétaire (plan, section 6, point 2) ;
      - les seuils de la section 5 (P95 du premier audio, coût par session) sont suivis sur les
        enregistrements d'exécution de production après la bascule ;
      - le critère d'acceptation (2) de l'action 3, test en aveugle à 5 personnes, est levé par cette
        décision.
- [ ] **GO sous conditions** : ______ .
- [ ] **NO-GO** : repli sur un autre fournisseur (Cartesia Sonic 3.6, premier en allemand selon
      Artificial Analysis, ou Inworld TTS-2) ou sur ElevenAgents (option B du plan).
