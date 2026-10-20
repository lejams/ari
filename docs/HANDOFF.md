# Reprise du travail dans une nouvelle session

Rédigé le 19 octobre 2026, à la fin d'une longue session d'exécution du plan
(`ARI_plan_claude_code.md`). Ce document est **autonome** : un nouvel agent, ou un autre compte
Claude, doit pouvoir reprendre sans autre contexte que le dépôt.

## 1. Lire dans cet ordre

1. `CLAUDE.md` : règles de travail, revue Opus obligatoire, tableau d'avancement, décisions du
   propriétaire, pièges connus. Il est chargé automatiquement par Claude Code.
2. `ARI_plan_claude_code.md` : les 12 actions, leurs invariants (section 2), leurs constantes
   (section 3) et les points réservés au propriétaire (section 6).
3. `AGENTS.md` : règles d'ingénierie du dépôt.
4. Selon l'action : `docs/LEARNING_CORE.md`, `docs/DEPENDENCIES.md` (modèles, prix, dépréciations),
   `docs/CAPABILITIES.md` (ce qu'ARI sait faire), `docs/spikes/exam-elevenlabs.md`.

Le rapport d'audit d'origine (PDF, en français, 27 pages) est **hors dépôt** :
`/home/smail/projects/ARI_audit_architecture_2026-10-14.pdf`. Le plan est auto-suffisant, le rapport
n'est pas nécessaire pour avancer.

## 2. État des branches (19 octobre 2026)

| Branche | Contenu | État | À faire |
|---|---|---|---|
| `main` (`b843f9f`) | Phase 0, actions 1, 2, 4 | à jour | rien |
| `chore/handoff` | ce document, `CLAUDE.md` et le plan, désormais versionnés | commitée localement | pousser et fusionner **en premier** |
| `feature/learning-core` | action 6, noyau `ari.learning` (commit `c682fb9`, `main` fusionné) | commitée localement, **non poussée** | pousser, **revue Opus**, puis fusionner |
| `spike/elevenlabs-tts` | action 3a, outil de mesure et protocole (commit `35d800a`) | commitée localement, **non poussée** | pousser (sauvegarde) ; **ne pas fusionner telle quelle** |

Commandes, depuis le dépôt, avec ses propres identifiants GitHub (l'agent n'en a pas) :

```sh
git push -u origin chore/handoff
git push -u origin feature/learning-core
git push -u origin spike/elevenlabs-tts
```

Ordre conseillé : `chore/handoff` (pour que `main` porte `CLAUDE.md` et le plan), puis
`feature/learning-core` après la revue Opus. Fusionner avec « Create a merge commit », pas en
« squash » : les branches empilées n'ont alors pas besoin d'être rebasées.

## 3. Revue Opus de l'action 6

Le brief est prêt : `docs/reviews/action6-opus-review-brief.md`. Le copier dans une session qui
tourne sur **Opus** (par exemple `/model opus`), sur la branche `feature/learning-core`. Le
verdict attendu est « approuvé », « approuvé avec réserves » ou « bloquant », avec des constats
reproductibles. L'action 6 ne doit pas être déclarée terminée avant que les constats soient traités.

## 4. Ce qui attend le propriétaire (aucun agent ne peut le faire)

1. **Pousser** les trois branches ci-dessus et fusionner dans l'ordre indiqué.
2. **Action 3a, mesures ElevenLabs** (échéance dure : `gpt-4o-mini-tts` s'arrête le **6 janvier
   2027**). Choisir 2 à 4 voix allemandes, mettre la clé dans le shell (`ELEVENLABS_API_KEY`, jamais
   dans le dépôt ni dans une conversation), lancer d'abord l'essai sur une phrase puis la mesure
   complète, organiser l'écoute à l'aveugle (5 personnes), remplir et valider
   `docs/spikes/exam-elevenlabs.md`. **L'action 3b (adaptateur de production) reste interdite tant
   que ce rapport n'est pas validé.**
3. **Aligner la configuration réelle sur `gpt-6-luna`** : `.env.example` (illisible pour l'agent) et le
   `.env` de production (`ARI_PATIENT_MODEL`, `ARI_EVALUATION_MODEL`, `ARI_CONTENT_MODEL`). Les défauts
   du code, de `docker-compose.yml` et de `deploy/env.example` sont déjà alignés.
4. **Atelier de 4 h avec le médecin** pour l'action 7 : environ 30 compétences FSP, définition d'une
   erreur critique, prérequis stricts. Sans ce livrable, l'action 7 ne peut pas commencer.
5. Plus tard : rubrique `fsp-arztbrief@1` et 30 lettres annotées (action 10), annotations du médecin
   pour la suite d'évaluation médicale (action 8), prestataire de paiement et textes juridiques
   (action 12).

## 5. Prochaines actions techniques

| Action | Dépend de | Revue Opus | Remarque |
|---|---|---|---|
| 5 Évaluations immuables (`evaluation_runs`) | rien | oui | migration sur une table append-only ; ne pas toucher aux observations de vocabulaire (c'est l'action 7) |
| 8 Cadre d'évaluation `ari.evals` | action 1 (faite) | non, sauf risque | contient le point 5bis : classer les configurations par coût parmi celles qui passent les seuils |
| 7 Taxonomie et observations | actions 5, 6, atelier médecin | oui | invariants 3 et 4 du plan |
| 9 Politique de sélection | action 7 | non, sauf risque | simulateur d'apprenants en CI |
| 10 Arztbrief | rubrique validée | non | |
| 11, 12 | voir le plan | oui | |

Règles qui s'appliquent à toutes : une action = une branche = une PR ; tests, lint et mypy verts ;
note de fin selon le modèle de la section 7 du plan ; **commit et push uniquement à la demande du
propriétaire**.

## 6. Décisions du propriétaire à ne pas rouvrir

Elles sont détaillées dans `CLAUDE.md`. En résumé :
- tous les modèles texte sur `gpt-6-luna` ; production en `gpt-realtime-2.1-mini` ;
- **aucun budget IA mensuel fixé a priori** ; le coût baisse par routage, cache et pré-génération,
  jamais par coupure ; ne jamais introduire de plafond en euros sans accord ;
- voix : on reste sur ElevenLabs, modèle `eleven_v4_turbo` ;
- une nouvelle révision d'un scénario remplace les précédentes (les variantes persona ou niveau d'un
  même protocole ne coexistent donc pas) ;
- pas de règle « un compte ne prend pas les deux décisions médecin et propriétaire » (retirée à sa
  demande).

## 7. Contexte produit hérité (ancienne mémoire de l'assistant, non portable d'un compte à l'autre)

- ARI prépare des médecins non germanophones à la FSP. Code écrit presque entièrement avec Codex ;
  cible de 1 000 à 2 000 utilisateurs ; l'apprenant type est A1 à B1 ; le propriétaire ne parle pas
  allemand et teste en français (`make dev-fr`, cas synthétique `cases/dev/ari_dev_fr.v1.yaml`).
- Contenu : le registre clinique (import, deux revues humaines, publication) est la seule source,
  jamais le code. Chaîne de production : PDF, IA, médecin, propriétaire, gold, brouillons, registre,
  deux revues, publication, le tout dans le back-office (`docs/BACKOFFICE.md`,
  `docs/CONTENT_PIPELINE.md`).
- Entraînement vocal : pipeline en appuyer-pour-parler ; le LLM ne fait que **choisir des faits**, le
  code rend la phrase rédigée. Examen : micro ouvert, un modèle speech-to-speech relayé côté serveur,
  audit a posteriori des faits (`FactAttributor`).
- Deux bases PostgreSQL logiques (`platform`, `content`). Le learner n'a pas les identifiants de
  `content`. PostgreSQL seul (plus de SQLite).
- Déploiement : un VPS, Docker Compose, Caddy. Runbook : `docs/DEPLOYMENT.md`.
- Ne jamais retirer de contenu visible : réorganiser, replier (préférence du propriétaire pour l'UI).

## 8. Environnement de travail

- **Aucun secret dans le dépôt.** Les fichiers `.env*` ne sont lisibles ni modifiables par les outils
  de l'agent.
- **Docker est inaccessible depuis WSL** sur ce poste. Les tests backend ont besoin d'un
  PostgreSQL : recette de secours sans droits administrateur dans `CLAUDE.md`, section « Pièges
  connus ». Sans lui, seuls les tests du noyau d'apprentissage et les tests web tournent.
- Contrôles : `make lint`, `make test-backend` (depuis la racine), `make test-web`,
  `make test-backoffice-web`, puis `alembic ... check` sur les deux chaînes.
- État local de la session précédente (éphémère) : un PostgreSQL lancé depuis un dossier temporaire
  `/tmp/claude-1000/.../scratchpad/pgdata`, port 5432. Il disparaît au redémarrage ; le relancer avec
  la recette de `CLAUDE.md`.
- Mesures du spike : l'outil écrit dans `var/spikes/elevenlabs/<date-heure>/` (ignoré par git).
- Sorties web : pas de `WebFetch` dans cette session ; la recherche web fonctionne. Les prix et
  dates de retrait cités dans `docs/DEPENDENCIES.md` viennent de sources secondaires, à confirmer.

## 9. Pour un nouvel agent : première heure

1. `git switch main && git pull`, puis lire les fichiers de la section 1.
2. Lancer un PostgreSQL local et `make lint` puis `make test-backend`.
3. Lire le tableau de `CLAUDE.md` : repérer la branche active et ce qui est bloqué par le
   propriétaire.
4. Ne rien commiter ni pousser sans demande explicite, s'arrêter sur tout point de la section 6 du
   plan.
