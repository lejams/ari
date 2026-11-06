# Passe multi-navigateurs

Mesure la landing, la connexion, l'application, la page vocale et le back-office sur **9 formats**
(1920, 1440, 1280, 1024, 768, 430, 390, 360, 320 px), en clair et en sombre, dans **Chromium** et **WebKit**.
Même mesure dans la page pour les deux moteurs (`measure.js`) : débordement horizontal, cibles tactiles,
champs sous 16 px (zoom d'iOS Safari), libellés tronqués, texte sous 11 px, axe-core, erreurs console.
`scrollperf.js` fait défiler la page et compte les images lentes (comparaison avant/après seulement : en
rendu logiciel les valeurs absolues ne disent rien).

## Lancer

```sh
make db-up   # ou une base locale ; puis, à la racine :
PYTHONPATH=backend/src python -m ari.demo --port 8010                 # côté apprenant, faux fournisseurs
PYTHONPATH=backend/src python web/tests/seed-e2e-accounts.py accounts.json
export ARI_QA_ACCOUNT_FILE=accounts.json NODE_PATH=<dossier avec playwright et axe-core>

node web/tests/cross-browser/run-chromium.mjs              # ONLY=m390,d1280  NOPERF=1  TARGET=bo  BASE=…
python web/tests/cross-browser/run-webkit.py                # voir ci-dessous
python web/tests/cross-browser/grade.py qa-chromium.json qa-webkit.json
```

Le compte doit déjà avoir un profil (le premier lancement de `practice-e2e.mjs` en crée un, ou passer
l'intégration une fois à la main). Pour le back-office : `python -m ari.backoffice_api.cli create-owner`,
accepter l'invitation (`POST /api/auth/invitations/<jeton>/password`), `TARGET=bo BASE=http://localhost:8100`
et `ARI_QA_BO_EMAIL` / `ARI_QA_BO_PASSWORD`.

### WebKit

Playwright télécharge WebKit depuis un CDN : sur un poste sans accès, `apt install webkit2gtk-driver xvfb`
et `pip install selenium` donnent WebKitGTK (le moteur de Safari, pas Safari : pas de barre d'adresse mobile,
pas de tactile, pas de `safe-area`). La fenêtre de MiniBrowser ne descend pas sous 447 px : les formats
étroits passent par une iframe (`frame.html`, à servir sur le port 8011 :
`python -m http.server 8011 --bind 127.0.0.1` dans ce dossier ; le runner l'appelle par le même nom d'hôte que `BASE`, sinon les cookies `SameSite=Strict` du back-office n'atteignent pas l'iframe). `ARI_QA_AXE` = chemin de `axe.min.js`,
`xvfb-run -a python run-webkit.py`.

## Note par format

`grade.py` part de 10 et retire : débordement −4 ; cibles tactiles −0,25 chacune (plafond −2) ; champs sous
16 px −1 ; libellés tronqués −1 ; texte sous 11 px −0,5 ; axe −2 (sérieux) ou −0,5 (autre) par règle ;
erreur console −1 (hors 401 de la sonde de session, normale hors connexion) ; défilement saccadé −1.
Cibles : 44 px de haut, 44 px de large (40 dans les rangées denses : barre du bas, pastilles de la semaine,
sélecteur de langue ; 34 à 320 px). Firefox n'est pas couvert par ce banc.
