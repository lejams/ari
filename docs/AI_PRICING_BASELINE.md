# ARI — baseline tarifaire OpenAI

Version tarifaire : `openai-2026-09-03`
Vérification : 3 septembre 2026.

Cette baseline sert à reproduire les estimations locales. Le portail OpenAI reste la
source de vérité de la facturation : remises contractuelles, arrondis, traitements
asynchrones et changements de prix peuvent créer un écart avec le calcul local.

## Tarifs encodés

Tous les tarifs de tokens sont exprimés en USD par million de tokens.

| Modèle | Entrée texte | Entrée texte cachée | Sortie texte | Entrée audio | Entrée audio cachée | Sortie audio |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-realtime-2.1-mini` | 0,60 | 0,06 | 2,40 | 10,00 | 0,30 | 20,00 |
| `gpt-realtime-2.1` | 4,00 | 0,40 | 24,00 | 32,00 | 0,40 | 64,00 |
| `gpt-5.6-luna` | 0,20 | 0,02 | 1,20 | — | — | — |
| `gpt-5.6-terra` | 2,00 | 0,20 | 12,00 | — | — | — |
| `gpt-4o-mini-tts` | 0,60 | — | — | — | — | 12,00 |

Tarifs STT selon l’unité officiellement documentée :

| Modèle | Unité | Tarif |
|---|---|---:|
| `gpt-transcribe` | minute d’audio transcrit | 0,0045 USD |
| `gpt-live-transcribe` | minute d’audio Realtime | 0,017 USD |

Sources officielles ouvertes lors de la vérification :

- [GPT-Realtime-2.1 mini](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini)
- [GPT-Realtime-2.1](https://developers.openai.com/api/docs/models/gpt-realtime-2.1)
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Comparaison GPT-5.6, dont Terra](https://developers.openai.com/api/docs/models/compare)
- [GPT-Transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe)
- [GPT-Live-Transcribe](https://developers.openai.com/api/docs/models/gpt-live-transcribe)
- [GPT-4o Mini TTS](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)

Les écritures de cache GPT-5.6 sont tarifées à 1,25 fois le prix de l’entrée non
cachée lorsqu’elles sont explicitement présentes dans le payload d’usage.

## Précision du calcul

Chaque exécution conserve le payload d’usage brut et produit : montant calculé,
statut, version tarifaire, unités, hypothèses et raison d’une inconnue.

- `exact` : toutes les unités nécessaires sont fournies et ventilées selon le tarif ;
- `estimated` : le montant repose sur une hypothèse documentée, par exemple l’absence
  d’un champ de cache interprétée comme une entrée non cachée ;
- `partial` : seules les composantes non ambiguës sont chiffrées ;
- `unknown` : aucune unité facturable fiable ne permet un montant.

Un total de cache Realtime sans ventilation texte/audio est `partial`, car les deux
modalités ont des tarifs différents. Les tokens cachés ventilés sont soustraits des
totaux de leur modalité avant de facturer le reliquat au tarif non caché.

Le STT utilise la durée entre limites VAD fournisseur. En l’absence de bornes de
segment fiables, les octets PCM accumulés peuvent inclure du silence : l’adaptateur
conserve alors le coût comme `unknown`. Il ne substitue jamais le temps total du tour,
l’attente LLM ou la lecture TTS à la durée audio transcrite.

L’adaptateur Speech streaming utilisé pour `gpt-4o-mini-tts` ne reçoit actuellement
pas les nombres de tokens texte et audio facturés. Il conserve les caractères et le
délai du premier chunk à des fins de diagnostic, mais ne transforme ni caractères ni octets
PCM en tokens : le coût TTS reste donc `unknown` (ou `partial` si une future réponse
fournit une seule des deux unités).

## Baseline historique

La session locale `45e40efc-4edc-44be-b4b7-5a28a36bb4b5` avait affiché environ
0,12 USD sur le portail pour six minutes en `realtime_economy`. Cette observation
reste un repère historique, pas un tarif ni une preuve de coût futur. Les anciens
champs `estimated_cost_usd` sont préservés par migration mais marqués `estimated`.

## Mise à jour

1. Ouvrir chaque page officielle correspondant exactement aux modèles du registre.
2. Créer une nouvelle version datée dans le calculateur, sans réécrire l’historique.
3. Ajouter les fixtures de payload cache/non-cache et ambiguës.
4. Exécuter les tests pricing, la migration, le benchmark offline et `alembic check`.
5. Comparer ensuite plusieurs sessions autorisées au portail avant toute projection
   business ou recommandation champion/challenger.
