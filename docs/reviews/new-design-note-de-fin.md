# Note de fin — refonte design (`new_design`)

Branche d'intégration : `claude/project-thread-fa9u4y` (refonte `new_design` fusionnée dans `main` du 3 novembre 2026, PR #57 de `new_design` incluse). Décisions du propriétaire du 3 novembre 2026 : le nouveau design remplace la direction A (Atkinson et violet retirés) ; mode sombre **B « Nuit bleue »** ajouté ; textes de la landing corrigés (voir « Intégration »).

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

## Intégration (3 novembre 2026)

- **Fusion** de `new_design` dans `main` (`practice-ui.mjs` : vues `programme` et `drill` gardées ensemble ; `index.html`).
- **Jetons** : `web/tokens.css` porte désormais les couleurs claires et sombres (boutons, cartes, statuts, aura, carte thermique). Les couleurs en dur de `practice.css` et `styles.css` passent par des jetons. L'écran Examen est toujours sombre (`body.exam-active`), avec les mêmes jetons que le mode sombre.
- **Mode sombre B** : suit le système (`prefers-color-scheme`) tant que l'apprenant n'a pas choisi ; un bouton (`[data-theme-toggle]`, `web/theme.js`) fixe clair ou sombre, gardé dans `localStorage` (`ari-theme`). Sur la landing, l'app, la connexion, la page vocale et le back-office (`backoffice-web/theme.js`, copie identique : autre origine). Les auras gardent leur teinte par page ; elles sont plus saturées en sombre.
- **Accessibilité** : bouton de démarrage de l'examen (règle `body.exam-active .primary` supprimée, jetons `--btn-bg` / `--btn-fg`), libellés mono (`--fg-faint` assombri), pastilles (`--accent-strong`, `--good-text`, `--violet-text`), texte accent sur fond doux. `practice-e2e.mjs` passe axe-core et le contrôle de débordement à 1280 et 390 px **en clair et en sombre** (huit vues), et teste le bouton de thème.
- **Placeholders retirés de l'app** : carte « Seuil de réussite » supprimée, carte FSP de la barre latérale masquée sans date d'examen, notes `[à définir]` retirées.
- **Barre mobile** : six entrées avec icône, libellés à 10 px.
- **Landing** : polices et jetons servis par le site (plus de Google Fonts, test `test_web_pages.py` adapté : seuls `/app`, `/fonts.css`, `/tokens.css`, `/theme.js` et la police Geist sont autorisés, aucun hôte tiers). Sélecteur FR/EN/DE masqué (seuls les menus étaient traduits). Textes : ligne « coût [à sourcer] » retirée, tons adoucis (« feedback rare », « oral peu »), barres `[x/y]` du bilan remplacées par les faits recueillis, Arztbrief et épreuves blanches marqués « bientôt », Arzt–Arzt présenté comme écrit, « les faits clés sont relevés à la fin de l'échange ». « Cas relus par des médecins » laissé tel quel (décision du propriétaire).
- **Back-office** : polices auto-hébergées (`backoffice-web/fonts/`), mode sombre, bouton de thème.

## Décisions ouvertes

1. **Seuil de réussite FSP** : non affiché dans l'app tant qu'il n'est pas fixé.
2. **i18n** : chrome de l'app en FR/EN/DE seulement ; le contenu reste en français.
3. **Arztbrief / Confrère** : Arztbrief hors périmètre (« bientôt » sur la landing), confrère = exercices Arzt–Arzt dans Consultation.
4. **Compte à rebours FSP** : branché sur la date d'examen du profil.
5. **Page vocale** : sans aura (fond uni) ; à décider si elle en reçoit une.
6. **Atkinson Hyperlegible** : les fichiers restent dans `web/fonts/` (anciens caches) ; à supprimer dans une PR de nettoyage.
7. **Chaînes en dur** de l'app (« Plan de la séance », « Vos axes », etc.) : pas dans `i18n.mjs`.
