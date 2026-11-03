# Note de fin — refonte design (`new_design`)

Branche : `new_design` (depuis `main`). Pas de fusion ni de déploiement.

## Surfaces

### Landing (`web/landing.html`)
- Reprise de la maquette `design/mockups/08-blynt-pouls.html` : hero avec pouls ECG, sections Pourquoi ARI / Une journée (scrollytelling) / Méthode / Évaluation, auras pastel, Geist + JetBrains Mono.
- CTAs branchés sur `/app` (contrat test : seul chemin local).
- FAQ Training vs Exam, périmètre et disclaimer (données illustratives, seuils `[à définir]`, pas de micro).
- Sélecteur FR / EN / DE sur le chrome de navigation.
- `prefers-reduced-motion` : animations coupées, pouls en tracé statique.

### App apprenant (`web/index.html`, `practice.css`, `i18n.mjs`, `fonts.css`, `tokens.css`)
- Chrome aligné sur `09-app.html` : sidebar Pouls, labels Aujourd’hui / Consultation / Bilan / Lexique / Progression / Programme / Examen, topbar, auras par vue, FR/EN/DE.
- **Layout Aujourd’hui** fidèle à la maquette : salutation + sous-titre, bandeau KPI (série / lexique / consultations / seuil), grille `g-dash` (plan de séance en timeline + compte à rebours FSP + axes), semaine compacte et parcours en bas.
- **Vue Programme** dédiée (`#programme`) : phases du parcours + grille hebdomadaire à 7 colonnes alimentée par `/api/program`.
- Données réelles (`/api/program`, cas, lexique, progression, historique) — aucun KPI inventé ; le seuil reste un placeholder de config.
- Contrats DOM conservés (`#recommendation`, `#week-days`) pour les tests.
- Polices auto-hébergées (Geist, JetBrains Mono) — pas d’appel tiers sur les pages authentifiées.
- Connexion et page vocale rétargetées sur le même système de tokens.

### Back-office Gold Content (`backoffice-web/`)
- Même système, version claire, plus dense : fond `#fafafa`, accent `#0070d5`, Geist + JetBrains Mono, rayons 6/10.
- Rôles, routes et écrans inchangés (documents, relecture, gold, registre, coûts, comptes).

## Lancer en local

```sh
python3 -m venv .venv && make install-locked
make db-up   # ou PostgreSQL local (pgserver) avec rôles ari_platform / ari_content
make migrate migrate-content
make dev                 # http://127.0.0.1:8000  (landing + app)
make backoffice          # http://127.0.0.1:8100
# démo fake + cas synthétiques :
PYTHONPATH=backend/src .venv/bin/python -m ari.demo --port 8010
```

Qualité : `make test-web`, `make test-backoffice-web`, `make lint`, `make test-backend`.

## Captures

| Surface | Fichier |
|---|---|
| Landing hero | `/opt/cursor/artifacts/screenshots/landing-hero.png` |
| Landing « Une journée » | `/opt/cursor/artifacts/screenshots/landing-journee.png` |
| Connexion | `/opt/cursor/artifacts/screenshots/login.png` |
| App Aujourd’hui | `/opt/cursor/artifacts/screenshots/app-aujourdhui.png` |
| App Programme | `/opt/cursor/artifacts/screenshots/app-programme.png` |
| App Consultation | `/opt/cursor/artifacts/screenshots/app-consultation.png` |
| App Lexique | `/opt/cursor/artifacts/screenshots/app-lexique.png` |
| App Progression | `/opt/cursor/artifacts/screenshots/app-progression.png` |
| App Bilan | `/opt/cursor/artifacts/screenshots/app-bilan.png` |
| App mobile | `/opt/cursor/artifacts/screenshots/app-mobile.png` |
| Back-office login | `/opt/cursor/artifacts/screenshots/backoffice-login.png` |
| Back-office dashboard | `/opt/cursor/artifacts/screenshots/backoffice-dashboard.png` |

## Décisions ouvertes

1. **Seuil de réussite FSP** : toujours `[à définir]` / paramètre de config — pas de valeur inventée.
2. **i18n** : chrome FR/EN/DE seulement ; le contenu pédagogique et la plupart des libellés métier restent en français (catalogue de chaînes produit à étendre si besoin).
3. **Arztbrief / Confrère** dans la maquette app : présents en navigation illustrative ; en production, Arztbrief hors périmètre, confrère = exercices Arzt–Arzt dans Consultation.
4. **Landing** : Google Fonts (Geist / JetBrains) conservés car la page publique ne peut pas charger d’assets locaux hors `/app`.
5. **Back-office** : Google Fonts (même contrainte d’origine différente) ; passage éventuel à des woff2 auto-hébergés dans `backoffice-web/fonts/`.
6. **Compte à rebours FSP** : branché sur `model.exam.weeks_left` / date d’examen du profil ; affiche « — » et `[date à saisir]` tant que la date manque — pas de stats inventées.
7. **Mode sombre landing** : retiré au profit de la maquette claire validée.
