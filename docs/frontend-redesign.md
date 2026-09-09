# Refonte frontend ARI

Référence visuelle en lecture seule : `ARI_frontend_concept.html` fourni le 8 septembre 2026. Les scripts et données de ce prototype ne sont pas utilisés comme moteur du produit.

## Correspondance vérifiée

| Écran | Frontend | Données et opérations existantes |
| --- | --- | --- |
| Onboarding et profil | index.html, practice-ui.mjs | GET /api/profile, POST /api/learners, PATCH /api/learners/{id}/goal ; cookie HttpOnly |
| Accueil et régularité | index.html, practice-ui.mjs | GET /api/history, GET /api/cases?approved_only=true, GET /api/exercises |
| Recommandation, échauffement, cas | index.html, practice-ui.mjs | Catalogue approuvé ; sélection explicitement expliquée, pas de moteur de calibration |
| Training / Examen vocal | voice.html, app.js | POST /api/sessions, GET /api/sessions/{id}, WebSocket /ws/sessions/{id}/voice, transport WebRTC ou pipeline existant |
| Exercice structuré | practice-ui.mjs, practice-client.mjs | POST /api/practice/runs, GET /api/practice/runs/{id}, answers, pause, resume, finish |
| Feedback vocal | voice.html, app.js | POST /api/sessions/{id}/end, analysis/retry ; évaluation existante |
| Feedback structuré | practice-ui.mjs | Réponse du run terminé, rubrique et preuves persistées |
| Historique et progression | practice-ui.mjs | GET /api/history et /api/progression ; séries séparées, aucune moyenne globale inventée |
| Vocabulaire | index.html et voice.html | Aides de vocabulaire par session et usage existants ; pas de carnet personnel persistant |

## Limites du backend

Le profil actuel conserve un objectif CEFR, pas un niveau mesuré. Il n’existe ni stockage de justificatif utilisateur, ni validation de certificat, ni consultation de positionnement calibrée, ni programme individualisé. Les préférences enrichies conservées sur l’appareil doivent être présentées comme locales. Voir `frontend-backend-proposal.md` pour les contrats à définir.

Le cookie de profil local ne constitue pas un système complet de compte, récupération ou synchronisation multi-appareil.

## Lancement

Depuis le dépôt : `make dev` utilise l’environnement et les fournisseurs configurés du produit. Aucun contenu du prototype ne doit être chargé dans le backend.

Pour une vérification isolée : `PYTHONPATH=backend/src python -m ari.demo --port 8012`. Cette commande existante crée une base temporaire, active explicitement des exercices synthétiques et force les fournisseurs factices. Les résultats de ce serveur de test ne sont pas des résultats pédagogiques réels. La base existante du produit n’est pas modifiée.

## Préservation

Le lien `.git` du dossier Downloads/ARI était déjà cassé au début du travail. Une archive avant modification et un manifeste SHA-256 ont été conservés dans `/private/tmp/ari-before-frontend.tar.gz` et `/private/tmp/ari-frontend-original-manifest.json`. Aucun historique Git n’a été réinitialisé.
