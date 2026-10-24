# Action 13 : audit UI/UX de l'application (passe « application »)

Rédigé le 24 octobre 2026 sur `main` en `eeb5ddb`. Périmètre : `web/` (application apprenant, page de
connexion et page de consultation vocale). La landing (`web/landing.html`) et le back-office relèvent
d'autres passes.

## 1. Méthode

- **Skills.** `ux-audit` et `design-audit` sont introuvables dans la session (recherche dans les skills
  du compte, aucun résultat). Comme le prévoit le plan (action 13, « Outils »), la grille de l'étape 1
  est appliquée à la main : parcours clés, états vides et d'erreur, accessibilité, clarté des textes en
  français. Aucun contenu de ces skills n'est supposé.
- **Environnement.** `python -m ari.demo` en mode fake (contenu synthétique `cases/dev`, PostgreSQL 16
  local), Chromium 1.62 piloté par Playwright, micro simulé (`--use-fake-device-for-media-stream`).
  Deux largeurs : ordinateur (1280 × 860) et mobile (390 × 844).
- **Parcours joué de bout en bout, deux fois (une par largeur).** Création de compte par l'onglet
  « Créer un compte », lien d'activation lu dans le journal du serveur, choix du mot de passe (essai
  trop court puis valide), onboarding en deux étapes (essai sans Land et sans spécialité), test de
  positionnement complet, accueil, bibliothèque (recherche sans résultat), page Examen, carnet,
  progression et historique vides, consultation vocale en entraînement (trois phrases, aide
  vocabulaire, fin et feedback), puis accueil, carnet, progression et historique remplis.
- **États d'erreur provoqués.** Seconde session vocale ouverte dans un autre onglet, trois examens
  d'affilée (quota de 2), coupure réseau dans l'application. Le micro refusé et l'échec du STT ne se
  reproduisent pas en mode fake (le micro n'y est pas ouvert) : constatés à la lecture du code
  (`web/app.js`), à confirmer sur un vrai compte.
- **Contrôles automatiques.** axe-core 4.10.3 (règles WCAG 2.0/2.1 A et AA, bonnes pratiques) sur chaque
  écran, taille des cibles tactiles visibles (< 44 px sur mobile), débordement horizontal de la page,
  ordre de tabulation sur l'accueil.
- **Pas encore fait.** Le passage sur un vrai compte de test avec les vrais fournisseurs (micro réel,
  STT, voix), et le test avec 3 à 5 médecins (étape 5 du plan, organisé par le propriétaire).

Gravité : **bloquant** empêche de terminer un parcours clé sans aide ; **majeur** le gêne nettement, ou
donne une information fausse ; **mineur** est un défaut de finition. Effort : **S** (moins d'une heure),
**M** (une demi-journée), **L** (plus).

## 2. Synthèse

Aucun constat bloquant en mode fake : un nouveau compte va de l'inscription au feedback de sa première
consultation sans erreur JavaScript. Les messages des limites structurelles (une session vocale à la fois,
quota d'examens) sont clairs et en français.

Onze constats majeurs, regroupés en quatre chantiers de correction proposés (section 5). Les trois
qui pèsent le plus sur la bêta :

1. Juste après le test de positionnement, l'accueil affiche « niveau non mesuré » et « Passer le test de
   niveau » (M1).
2. Pour un compte créé en cours de semaine, les jours passés du programme sont marqués comme manqués, et
   la « prochaine étape » propose un exercice d'un jour passé avec « rien de prévu aujourd'hui » (M2).
3. Sur mobile, la page de consultation vocale et la page Progression débordent horizontalement (M4).

Le temps jusqu'à la première consultation (critère d'acceptation) dépend surtout de l'ordre du parcours :
le test de positionnement (environ quinze minutes) est imposé avant l'accueil sans bouton « plus tard »,
et l'accueil propose ensuite un exercice texte plutôt qu'une consultation (M3, question au propriétaire).

## 3. Constats majeurs

### M1. L'accueil ignore le niveau qui vient d'être estimé
- **Écran.** Accueil, carte « Votre parcours », après « Retour à l'accueil » sur le résultat du test.
- **Constat.** Le profil serveur porte bien `estimated_level: A2`, mais l'accueil affiche « Objectif
  personnel C1 · niveau non mesuré » et le bouton « Passer le test de niveau ». Un rechargement de page
  corrige l'affichage. Cause : la dernière réponse du test termine la tentative sans relire le profil
  (`web/practice-ui.mjs`, gestionnaires `placement-choice-form` et `placement-text-form` ; seul
  « Arrêter et voir mon estimation » appelle `restoreProfile`).
- **Capture.** [03-accueil-apres-test.jpg](ux-app-audit/03-accueil-apres-test.jpg)
- **Correction.** Relire le profil quand `renderPlacement` reçoit une tentative terminée. Test DOM ou e2e :
  l'accueil affiche le niveau estimé sans rechargement.
- **Effort.** S.

### M2. Jours d'avant l'inscription marqués « manqués », prochaine étape tirée du passé
- **Écran.** Accueil, carte « Cette semaine » et carte « Votre prochaine étape ».
- **Constat.** Compte créé le samedi : lundi, mercredi et vendredi apparaissent en pointillés (état
  `missed`), avec « 30 / 210 min ». La carte principale propose « Fachbegriffe · … » avec « Prérequis ·
  niveau A2 · rien de prévu aujourd'hui ». Cause : `application/services/program.py` marque `missed` tout
  créneau antérieur à aujourd'hui et prend le premier créneau ouvert, manqué compris, comme `next`.
  Pour un médecin qui découvre l'application, le premier écran annonce un retard et se contredit.
- **Capture.** [03-accueil-apres-test.jpg](ux-app-audit/03-accueil-apres-test.jpg)
- **Correction proposée.** Les jours antérieurs à la création du profil ne sont ni manqués ni comptés
  dans le budget de la semaine ; `next` préfère un créneau du jour ou à venir, puis un manqué. Le
  programme est versionné (`PROGRAM_RULES_VERSION`) : nouvelle version des règles, tests du service.
- **Effort.** M.

### M3. Chemin vers la première consultation (question au propriétaire)
- **Écrans.** Fin d'onboarding, test de positionnement, accueil.
- **Constat.** Après l'onboarding, le test (environ quinze minutes) s'ouvre sans bouton « plus tard » ;
  le seul moyen de le différer est la navigation latérale. Pendant les questions à choix, « Arrêter et
  voir mon estimation » est masqué. Le résultat propose « Retour à l'accueil », puis l'accueil propose un
  exercice texte Fachbegriffe (phase « Prérequis » pour un niveau A2), pas une consultation. Mesuré en
  mode fake : inscription → première consultation = 2 écrans de compte, 2 d'onboarding, 14 écrans de test
  (8 questions, 4 écoutes, 2 prises de parole), puis Bibliothèque → carte → page vocale.
- **Capture.** [02-placement-resultat.jpg](ux-app-audit/02-placement-resultat.jpg)
- **Correction proposée.** Ajouter sur l'intro du test un bouton secondaire « Faire le test plus tard » et,
  sur le résultat, un appel à l'action vers la prochaine étape du programme. Ajout seul, rien n'est
  retiré.
- **Décision du propriétaire (24 octobre 2026).** L'ordre du parcours ne change pas (test d'abord, règles
  du programme inchangées) ; on ajoute « plus tard » sur l'intro du test et l'appel à l'action vers la
  prochaine étape sur le résultat. Le test avec les médecins dira si l'ordre doit changer.
- **Effort.** S.

### M4. Débordement horizontal sur mobile
- **Écrans.** Consultation vocale (`voice.html`, 35 px) et Progression (296 px), à 390 px de large.
- **Constat.** Sur la page vocale, la colonne de contexte et la carte de consultation dépassent à droite ;
  sur Progression, le tableau de couverture (`#heatmap`) élargit la colonne de la grille, et toutes les
  cartes qui suivent sont décalées. L'e2e ne vérifie le débordement que sur la bibliothèque.
- **Captures.** [04-voix-mobile-debordement.jpg](ux-app-audit/04-voix-mobile-debordement.jpg),
  [06-progression-mobile.jpg](ux-app-audit/06-progression-mobile.jpg)
- **Correction.** `grid-template-columns: minmax(0, 1fr)` pour la grille mobile de `practice.css` (le
  défilement reste dans `.heatmap-wrap`) ; même correction sur la grille mobile de `.voice-stage`
  dans `styles.css` (`1fr` en dessous de la largeur de rupture). Étendre la vérification de débordement de l'e2e à toutes les pages.
- **Effort.** S.

### M5. Messages d'erreur techniques en anglais
- **Écrans.** Toute l'application hors ligne ; page vocale si le micro est refusé.
- **Constat.** Hors ligne : « Failed to fetch Vous pouvez réessayer ; aucune réussite n'est supposée. »
  Micro refusé (code de `openMicrophone`, non reproductible en fake) : le statut affiche le message brut
  du navigateur (« Permission denied »), sans dire comment autoriser le micro ni proposer de réessayer.
  Le plan liste ces deux états parmi ceux à couvrir.
- **Capture.** [09-hors-ligne.jpg](ux-app-audit/09-hors-ligne.jpg)
- **Correction.** Traduire les erreurs réseau dans `PracticeClient.api` et dans `api()` de `app.js`
  (« Connexion impossible. Vérifiez votre réseau, puis réessayez. »). Pour `NotAllowedError` et
  `NotFoundError` du micro, un message en français qui explique où autoriser le micro, et le bouton
  « Commencer la consultation » reste disponible. Tests unitaires sur le client.
- **Effort.** S.

### M6. Quota d'examens : découvert seulement au démarrage
- **Écrans.** Page Examen, page vocale en mode Examen.
- **Constat.** Le message serveur est clair (« Quota d'examens du mois atteint (2/2). Il se renouvelle le
  01/11/2026. L'entraînement reste disponible. »), mais il n'apparaît qu'en petit dans la ligne d'état
  de la page vocale, à côté d'un bouton « Commencer la consultation » toujours actif. La page Examen
  propose toujours « Commencer en Examen → » et n'indique jamais combien d'examens il reste.
- **Captures.** [07-quota-examens.jpg](ux-app-audit/07-quota-examens.jpg),
  [08-page-examen-quota-epuise.jpg](ux-app-audit/08-page-examen-quota-epuise.jpg)
- **Correction.** Afficher « Examens ce mois-ci : n sur 2 » sur la page Examen (lecture seule, depuis une
  donnée serveur existante ou un petit point d'API), et, quand le quota est épuisé, un encart avec la date
  de renouvellement et un lien « Faire ce cas en Training ». Aucune valeur de quota n'est changée.
- **Effort.** M.

### M7. Identifiants techniques et jargon visibles par l'apprenant
- **Écrans.** Historique, Progression, feedback vocal, page vocale.
- **Constat.**
  - Historique : « Session vocale · ARI-DEV-FR@4 », « Arzt–Patient · training · Terminé ».
  - Progression, « Détails par contenu et par version » : « Contenu ari-dev-fr-arzt_patient@4 ·
    rubrique ari-dev-fr-voice-rubric@1 », « Évaluation pondérée : 0/1 poids satisfaits ».
  - Feedback vocal : « Prononciation : non évaluée dans ce POC » ; score affiché « Provisoire » en
    grand, à la place d'un score.
  - Mélange anglais et français : « Training » et « Exam » (page vocale), « training » en minuscules,
    « Objectif CEFR » (le sigle français est CECRL), « transcript ».
- **Captures.** [10-historique.jpg](ux-app-audit/10-historique.jpg),
  [05-voix-feedback.jpg](ux-app-audit/05-voix-feedback.jpg),
  [06-progression-mobile.jpg](ux-app-audit/06-progression-mobile.jpg)
- **Correction.** Titre du cas au lieu de l'identifiant, libellés de mode en français, « POC » remplacé
  par une phrase neutre (« La prononciation n'est pas encore évaluée. »). Les identifiants de version
  restent disponibles, repliés dans un « Détails techniques » (réorganiser, ne rien retirer). Le choix
  « Training » contre « Entraînement » est un texte produit : à confirmer par le propriétaire.
- **Effort.** S à M.

### M8. Accessibilité : rôles ARIA invalides sur le programme et le feedback
- **Écrans.** Accueil (pastilles de la semaine), feedback vocal (tuiles), page vocale.
- **Constat (axe, impact critique ou sérieux).**
  - Les pastilles de jour sont des `button` avec `role="listitem"` et `aria-pressed` : axe signale un
    attribut interdit (critique) et un rôle inadapté. Même rôle `listitem` sur les tuiles du feedback.
  - `#transcript` porte un `aria-label` sans rôle, et la zone défilante du transcript n'est pas
    atteignable au clavier ; idem pour `.heatmap-wrap` sur Progression.
  - L'`aside` de la page vocale est imbriqué dans `main` (landmark).
- **Correction.** Envelopper chaque bouton dans un `div role="listitem"` (ou retirer `role="list"`),
  `role="log"` sur le transcript, `tabindex="0"` et un libellé sur les zones défilantes, sortir l'`aside`
  de `main`. Ajouter le contrôle axe à l'e2e (étape 4 du plan).
- **Effort.** S.

### M9. Contrastes insuffisants sur les petits textes
- **Écrans.** Accueil (sur-titre de la carte principale), Progression (calendrier, 32 nœuds sur
  ordinateur), page vocale (étiquettes « PATIENT » et « VOUS »), feedback.
- **Constat.** Gris `#657180` sur fonds teintés, entre 4,2:1 et 4,5:1 pour des textes de 10 à 12 px
  (seuil AA : 4,5:1). Le texte d'aide général (`--muted` sur le fond de page) passe de justesse.
- **Correction.** Assombrir `--muted` d'un cran (par exemple `#5a6676`) dans `practice.css` et la variable
  équivalente de `styles.css`. Changement de teinte minime, sans toucher à l'identité visuelle.
- **Effort.** S.

### M10. Validation des formulaires par les bulles du navigateur
- **Écrans.** Onboarding (Land, spécialité), choix du mot de passe.
- **Constat.** Les champs obligatoires ne sont pas signalés comme tels. L'erreur n'apparaît que dans la
  bulle native du navigateur, dans la langue du navigateur (« Please select an item in the list. »), et
  disparaît au bout de quelques secondes ; rien n'est annoncé dans la zone d'état.
- **Capture.** [01-onboarding-validation.jpg](ux-app-audit/01-onboarding-validation.jpg)
- **Correction.** Marquer les champs obligatoires, afficher un message en français sous le champ
  (`aria-describedby`, `aria-invalid`) et y placer le focus.
- **Effort.** S.

### M11. Feedback vocal : actions en double, sortie ambiguë
- **Écran.** Page vocale après « Terminer ».
- **Constat.** Trois blocs d'actions : « Refaire ce cas » et « Nouvelle consultation » dans la carte
  d'appel, « Refaire ce cas » et « Réviser mon carnet » sous les tuiles, et trois liens en pied de
  feedback (« Réviser mon carnet » une seconde fois). Le lien en haut reste « Quitter la session » alors
  que la session est terminée. Le choix de mode Training/Exam reste affiché (désactivé) après la fin.
- **Capture.** [05-voix-feedback.jpg](ux-app-audit/05-voix-feedback.jpg)
- **Correction.** Une seule rangée d'actions après le feedback, « Retour à l'accueil » en haut une fois la
  session finie. Réorganisation seulement : chaque action reste accessible.
- **Effort.** S.

## 4. Constats mineurs (listés, sans PR dédiée)

| # | Écran | Constat | Correction | Effort |
|---|---|---|---|---|
| m1 | Carnet | `.sr-only` n'existe pas dans `practice.css` : les libellés « Rechercher un mot » et « Filtrer par origine » s'affichent. [11-carnet.jpg](ux-app-audit/11-carnet.jpg) | Copier la règle de `styles.css` | S |
| m2 | Mobile | Cibles tactiles sous 44 px : pastilles de la semaine (36 px de large), radios d'onboarding et du test (13 px, le libellé compense), « Quitter la session » (18 px de haut), liens « tour n » du feedback (17 px), logo « ARI » (19 px) | Agrandir la zone cliquable | S |
| m3 | Navigation | La barre latérale est active pendant l'onboarding, avant qu'un profil existe | Masquer ou désactiver la navigation pendant l'onboarding | S |
| m4 | Navigation mobile | « Historique » absent de la barre du bas (accessible depuis Progression) | Lien depuis l'accueil, ou sixième entrée | S |
| m5 | Historique vide | Texte « Choisissez un exercice pour commencer » sans bouton | Bouton vers la bibliothèque | S |
| m6 | Résultat du test | Titre « A2 · A2 · survie » (niveau répété, étiquette « survie » peu claire) ; les deux prises de parole affichent la même transcription en fake | Titre « Niveau estimé : A2 », libellé de bande explicite | S |
| m7 | Identité visuelle | Deux typographies : Atkinson Hyperlegible Next sur la connexion (chargée depuis Google Fonts), Inter déclarée par l'application et la page vocale mais jamais chargée, donc remplacée par la police système selon l'appareil | Décision d'identité visuelle, réservée au propriétaire | — |
| m8 | Lien d'activation | Ouvert dans l'onglet déjà sur `/connexion`, le lien `#jeton=…` ne fait rien jusqu'au rechargement (changement de fragment non écouté) | Écouter `hashchange` sur la page de connexion | S |
| m9 | Bibliothèque | Le choix Training/Examen se fait avant le cas, puis se refait sur la page vocale | Garder un seul choix, l'autre en lecture seule | S |
| m10 | Accueil | Le résumé de profil « Bayern · 30 min/jour · niveau B2 auto-évalué » cohabite avec un niveau estimé A2 sans explication | Une phrase qui distingue déclaré et estimé | S |

## 5. Corrections proposées, une PR par chantier

| Chantier | Constats | Taille | Remarques |
|---|---|---|---|
| A. Accueil et programme justes après l'onboarding | M1, M2, M3, m10 | M | touche `program.py` (nouvelle version des règles) et `practice-ui.mjs` |
| B. Mobile et accessibilité | M4, M8, M9, m1, m2 + contrôle axe et débordement dans l'e2e | S à M | sans changement de comportement visible |
| C. Messages et états d'erreur | M5, M6, M10, m5, m8 | M | M6 peut demander un petit point d'API en lecture |
| D. Textes et clarté | M7, M11, m3, m4, m6, m9 | S à M | libellés de mode à confirmer par le propriétaire |

Hors chantier, en attente de décision : m7 (identité
visuelle).

L'étape 4 du plan (e2e de tous les parcours en mode fake, captures mobile et ordinateur, en CI) se fait
avec le chantier B pour la partie débordement et axe, puis s'étend au fil des autres chantiers. axe-core
est une dépendance de développement légère (un fichier JavaScript, sans dépendance transitive).

## 6. Reste à faire pour cette passe

- Rejouer l'audit sur un vrai compte avec les vrais fournisseurs : micro réel et refusé, STT en échec,
  voix, examen en micro ouvert.
- Test avec 3 à 5 médecins de la cible (étape 5), à voix haute, inscription → première consultation,
  sans aide ; mesurer le temps médian jusqu'à la première consultation.
- Refaire la passe sur les écrans modifiés par les actions 9, 10 et 12 quand ils arrivent.
- Back-office (`backoffice-web/`), en second, comme le prévoit le plan.
