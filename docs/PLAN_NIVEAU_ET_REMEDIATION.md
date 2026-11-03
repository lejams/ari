# Plan : calibrer le niveau et cibler les exercices de langue

Rédigé le 2 novembre 2026 (`main` en `81357f4`). **Document de cadrage, aucun code écrit.** Il sera
fusionné avec la première PR de la fonctionnalité (règle du propriétaire du 25 octobre 2026 : pas de
PR séparée pour un document). Les points marqués **[D1]**, **[D2]**… sont des décisions du propriétaire
(section 8).

## 1. Direction produit (Ismail, 2 novembre 2026)

- Les utilisateurs veulent surtout progresser **en allemand comme langue** ; la FSP note surtout la
  langue, la médecine doit seulement ne pas être absurde (« mal au cœur » et « mal à l'orteil »).
- Les exercices ne sont **pas** des extraits des protocoles validés. Les exercices de langue
  (grammaire, registre, reformulation, écoute) peuvent être générés librement ; la médecine est un
  décor, avec un contrôle de cohérence grossier.
- Niveau minimal des apprenants : B1 (jusqu'à C1). Un test de niveau calibre l'arrivée, et les
  erreurs de langue détectées nourrissent la suite (remédiation).

Conséquence : le document « ARI Code » (exercices dérivés du gold, circuit créateur → validateur →
relecture médecin) devient secondaire. On en garde deux idées : **« le LLM propose, le code décide »**
(contrôles mécaniques avant tout) et **un second contrôle indépendant** pour ce qui est généré.

## 2. Ce que dit le dépôt (vérification du document ARI Code)

| Affirmation | Verdict | Détail |
|---|---|---|
| `learner_model.py` ne charge pas les erreurs de langue, donc `program-rules-v3` ne s'en sert pas | **Confirmé** | `LearnerModelService.compute` lit couverture des sections, empathie, carnet, activité. Les `language_errors` ne sont lues que par `progress_axes.py` (axe « Langue » affiché, erreurs récurrentes à partir de 2) : elles ne pilotent rien. |
| Blocage de 14 jours dans `mastery.py:59` | **Confirmé, mais sans effet aujourd'hui** | `critical_window_days = 14.0`, appliqué par `_blocked` (l. 273) aux seules compétences `critical`. Aucune compétence n'est marquée critique (la taxonomie est l'action 7) et **aucun code applicatif n'importe `ari.learning`** : c'est une bibliothèque non branchée. La règle est aussi écrite dans `docs/LEARNING_CORE.md` (l. 49, 101, 144) et dans le plan (l. 230). |
| Un seul type d'exercice, `practice-exact-answer-v1` | **Confirmé** | `PracticeSpecification` : questions `presentation`, `followup`, `term_definition`, critères à `accepted_answers`. Tout exercice est un `ClinicalBundle` issu d'un protocole gold. |
| `PHASES` sans `arztbrief` | **Confirmé** (hors périmètre ici) | `content/domain/bundles.py:21`. |
| « Le test de niveau existe » | **Plus fort que dit** | Voir section 3 : il est déjà construit de bout en bout. |

Deux précisions que le document d'ARI Code ne fait pas :

- Les erreurs de langue sont stockées **avec la citation et la correction collées dans un seul texte**
  (`« quote » → « correction » : explication`, `evaluation.py:258`). La structure (citation,
  correction) n'est pas conservée. Or la remédiation en a besoin.
- Le niveau n'est **jamais réajusté** après le test : `estimated_level` n'est écrit que par
  `PlacementService.finish`. Il faut le dire clairement : « réajusté au fil des séances » est un
  objectif, pas l'existant.

## 3. Le test de niveau existe déjà : il faut le calibrer, pas le construire

`placement-staircase-v1` (`application/services/placement.py`, `domain/placement.py`) fait déjà :
QCM adaptatif (escalier, 14 questions maximum, arrêt à 3 renversements), 4 écoutes, 2 prises de
parole transcrites puis notées par le LLM (`placement-speaking-v1`), niveau global = la compétence
mesurée la plus faible, copié dans le profil (`estimated_level`). L'onboarding l'annonce, la page
existe, « Faire le test plus tard » existe, et `program-rules-v3` n'ouvre aucune séance vocale tant
qu'il n'y a pas de niveau (phase `positionnement`).

Écarts avec ce qu'Ismail veut :

| # | Écart | Preuve |
|---|---|---|
| E1 | **Pas de jeu allemand publié connu.** Le jeu de développement est en français (`cases/dev`), le jeu allemand est une démo synthétique « non relue par un linguiste » (`cases/demo`). Sans jeu publié, l'apprenant reçoit « Aucun test de niveau publié ». **À vérifier sur le serveur.** | `placement.py:150` |
| E2 | **C1 impossible.** Aucun item C1 dans les deux jeux, et le schéma n'exige les niveaux que de A1 à B2 (`REQUIRED_LEVELS`). Le résultat est borné à B2 alors que la cible va jusqu'à C1. | `domain/placement.py:29` |
| E3 | **Étendue inadaptée.** L'échelle va de A1 à C2 et exige des items A1 inutiles pour un public B1+. Le plancher utile est « sous B1 » (déjà géré par la phase `prerequis`). | idem |
| E4 | **Pas de points faibles.** Le QCM distingue seulement « vocabulaire » et « grammaire » ; l'oral rend un niveau et jusqu'à 4 observations en texte libre, sans catégorie. Impossible d'amorcer le programme sur « genre » ou « cas ». | `SpeakingRatingSchema` |
| E5 | **4 QCM par niveau = minimum du schéma**, soit une estimation fragile (la doc recommande 6 à 8). | `docs/CLINICAL_CASES.md` |
| E6 | **Durée annoncée : environ 15 minutes** (texte de l'onboarding), pas 5 à 8. | `web/index.html:38` |
| E7 | **Aucun réajustement ensuite** (section 2). | |

### Ce qu'on fait (PR « Test de niveau calibré B1–C1 »)

1. **Échelle B1–C1.** Items exigés de A2 (sonde « sous B1 ») à C1 ; le résultat peut sortir C1.
   Nouvelle version de méthode `placement-staircase-v2`, `v1` conservée pour relire les tentatives
   passées.
2. **Jeu allemand authentique**, 8 QCM par niveau (A2 à C1), 2 écoutes par niveau B1 à C1, 2 prises
   de parole. Chaque QCM de grammaire porte une **catégorie** de la liste fermée déjà utilisée par
   l'évaluateur : `gender`, `case`, `verb_form`, `word_order`, `word_choice`, `register`. Le décor est
   médical quand c'est naturel, jamais un savoir médical à connaître.
   Rédaction par Claude, **revue linguistique par une personne germanophone** (le circuit existe :
   `placement import` / `review` / `publish`) **[D3]**.
3. **Points faibles.** Résultat = niveau + au plus 3 points faibles, calculés par code :
   - écrit : taux de réussite par catégorie sur les QCM taguées (seuil minimal de 2 items par
     catégorie pour en parler) ;
   - oral : le prompt `placement-speaking-v2` renvoie en plus des `language_errors` au même schéma
     que l'évaluateur (`LanguageErrorSchema`), **vérifiées par `verify_language_errors`** (la
     citation doit figurer dans la transcription, sinon l'erreur est écartée). Aucun appel de plus.
   - Un changement de prompt exige un rapport `ari.evals` (invariant 5) : jeu de monologues
     synthétiques construit comme `build_synthetic_v3.py`, mêmes seuils de précision.
4. **Parcours.** Le test reste proposé à la fin de l'onboarding avec « plus tard » (décision M3 du
   24 octobre). Tant qu'il n'est pas passé, le programme ne propose que test, carnet et
   Fachbegriffe (déjà le cas). Texte de durée corrigé selon **[D1]**.
5. **Le résultat alimente le programme** : les points faibles deviennent les premiers axes de
   remédiation (section 4), sous forme de `placement_weak_points` dans le modèle apprenant.

Ce que la PR ne fait pas : l'écoute adaptative, l'orthographe, la prononciation (aucune mesure).

## 4. Remédiation : relier ce que l'apprenant rate à ce qu'il travaille

*État au 2 novembre 2026 : la PR 1 (4.1 à 4.3, niveau 1) est écrite dans la branche
`claude/project-thread-rwidc9`. Écart avec le plan ci-dessous : elle ajoute la table
`language_drill_runs` (migration `0008`), car le créneau du programme doit savoir qu'un exercice est
fait ; le niveau de travail par catégorie (4.4) reste pour la PR 3.*

### 4.1 Données (PR « Remédiation branchée »)

- `EvidenceObservation` gagne `quote` et `correction` (optionnels). `session-evaluation-v5` ;
  les v2 à v4 restent lisibles (`repository._evaluation`). Les anciennes sessions n'ont pas ces
  champs et sont ignorées par la remédiation (on ne parse pas le texte collé).
- `learner-model-v2` ajoute un **profil de langue** calculé sur les 5 dernières séances terminées
  (même fenêtre que `RECENT_SESSIONS`, hors comptes d'évaluation) : par catégorie, nombre d'erreurs,
  erreurs récurrentes (≥ 2, seuil déjà utilisé par `progress_axes.py`), dernières citations avec leur
  correction, plus les points faibles du test de niveau. Signaux déterministes uniquement, rien
  n'est stocké.

### 4.2 Programme (`program-rules-v4`)

- Nouveau type de créneau `language_drill` (10 minutes, priorité entre la consultation et les
  Fachbegriffe), **un seul axe par jour** : la catégorie qui a le plus d'erreurs récentes, à défaut
  (une fois la PR du test de niveau fusionnée) le premier point faible du test. Aucun créneau si rien n'est établi (pas de remédiation inventée).
- La justification affichée cite les faits : « 4 erreurs de cas dans vos 3 dernières séances ».
- Le budget en minutes (`minutes_per_day`) et la règle « créneau manqué » restent inchangés.

### 4.3 L'exercice, en deux niveaux de fidélité

**Niveau 1 : « Votre phrase » (aucune génération).** L'exercice rejoue les erreurs réelles de
l'apprenant : on lui montre sa propre phrase, il la reformule, le code compare à la correction
(comparaison normalisée existante, `domain/text.py`). Pertinence maximale, zéro invention. Une
réponse différente de la correction est « non reconnue », jamais notée comme échec (il peut exister
une autre forme correcte) : on affiche la correction et on laisse l'apprenant se situer. Limite
honnête : **la justesse des corrections de l'évaluateur n'est pas mesurée** (`evaluation-language`
mesure la détection, pas la correction). La PR ajoute cette métrique au rapport, d'abord sans seuil.

**Niveau 2 : exercices générés par niveau et catégorie** (section 5), pour la pratique quand
l'apprenant n'a pas (assez) d'erreurs réelles ou veut s'entraîner plus.

Moteur d'exécution : un domaine léger `LanguageDrill` (items, réponses, correction déterministe),
**séparé** de `PracticeRun`, qui est lié à un `ClinicalBundle` et au gold. Le résultat (catégorie,
niveau, réussi ou non) alimente le profil de langue ; il est conçu pour devenir une source
d'observations de `ari.learning` quand l'action 7 sera faite, sans dépendre d'elle.

### 4.4 Niveau de travail par catégorie

`estimated_level` reste celui du test et n'est jamais écrasé en silence. Le modèle apprenant calcule
en plus un **niveau de travail par catégorie** (règle déterministe `working-level-v1` : on monte
d'un niveau après au moins 10 items récents réussis à 80 % ou plus, on descend sous 50 %, plafonné à
C1, plancher B1). C'est lui qui règle les exercices générés **[D8]**.

## 5. Génération d'exercices de langue (PR « Banque d'exercices de langue »)

Risque principal : **enseigner un allemand faux**. Le dispositif est donc : banque pré-générée,
versionnée et tracée, tirée à l'exécution (pas de génération en direct : coût, latence, et on peut
retirer un item).

1. **Spécification d'un item** : catégorie, niveau B1/B2/C1, format (choix à trous, correction d'une
   phrase fautive, reformulation de registre), décor médical, phrase, clé, distracteurs, explication
   en français. Le lexique du carnet de l'apprenant peut fournir les mots du décor.
2. **Générateur** (LLM, sortie par schéma strict) produit par lots hors ligne.
3. **Contrôles mécaniques** (code, aucun modèle) : schéma valide ; clé présente une seule fois dans
   les options ; un seul trou ; distracteurs distincts de la clé ; pas de doublon dans la banque ;
   longueur ; aucune coordonnée personnelle (module `clinical_privacy` existant).
4. **Résolution à l'aveugle par un second modèle** (autre fournisseur ou autre famille, prompt
   différent) **[D5]** : il résout l'item sans voir la clé. L'item n'est gardé que si le second
   modèle trouve la clé **et** affirme qu'une seule réponse est correcte. Le même second modèle
   rend un verdict grossier « énoncé médicalement plausible : oui/non », sans avis clinique.
5. **Relecture humaine par échantillon** : tous les items des deux premiers lots, puis un
   échantillon **[D6]**. Chaque item garde sa provenance (prompt, modèle, version, hash, verdicts).
6. **Bouton « Signaler une erreur »** dans l'exercice : un item signalé est retiré de la
   circulation jusqu'à revue. C'est le filet le moins cher contre un défaut non vu.
7. **Mesure** : suite `ari.evals` `language-exercises@1` sur le taux de fausse acceptation du
   contrôle à l'aveugle (items volontairement faux construits par code, comme les jeux synthétiques
   existants). Premier objectif : zéro item faux passant les contrôles sur le jeu de test.

**Stockage : base `platform`** (celle des apprenants, des sessions et des registres de cas et de
tests de niveau), pas la base `content` (pipeline des protocoles gold, rôle de base de données
séparé). Banque : table `language_exercises`, alimentée par une commande en ligne de commande comme
`placement import`. Réponses et résultats de l'apprenant : tables `language_drills`. Table d'ajout seul avec migration (même discipline que `evaluation_runs`), statut
`active | withdrawn`, jamais de modification d'un item publié.

## 6. Verrou de 14 jours (PR « Maîtrise : retirer le blocage de 14 jours »)

À retirer, car la durée est arbitraire (ni mesurée, ni justifiée par le rapport d'origine).
Remplacement proposé, `mastery-beta-v2` (version de modèle incrémentée, `v1` rejouable) :

- supprimer `critical_window_days` et `_blocked` (par des échecs récents) ;
- une compétence critique qui subit un échec après avoir été maîtrisée passe en `recheck` et ne
  redevient `mastered` qu'après `critical_recheck_successes` (2 par défaut) réussites sur des items
  distincts et des essais distincts **postérieurs à l'échec**, sans condition de durée ;
- mettre à jour `docs/LEARNING_CORE.md` (l. 49, 101, 144), le plan (l. 230) et les tests de
  propriétés (`test_learning_mastery.py`, `test_learning_replay.py`).

Honnêtement : **sans effet visible aujourd'hui** (bibliothèque non branchée, aucune compétence
critique). Cela touche au calcul de maîtrise de l'action 6 : je recommande une **revue Opus ciblée**
(petit diff) **[D7]**.

## 7. Découpage en PR (une fonctionnalité = une PR = un commit sur `main`)

| # | PR | Dépend de | Contenu | Revue Opus ? | Attente humaine |
|---|---|---|---|---|---|
| 1 | **Remédiation branchée** | rien | champs structurés des erreurs, `learner-model-v2`, `program-rules-v4`, créneau `language_drill`, exercice « Votre phrase », métrique de justesse des corrections (rapport seulement) | non (migration `0008_language_drill_runs` : une table qui s'ajoute, sans toucher à l'existant ; revue Codex comme les autres) | aucune |
| 2 | **Test de niveau calibré B1–C1** | rien (code) ; jeu de questions pour la mise en ligne | `placement-staircase-v2`, jeu allemand, catégories, points faibles, `placement-speaking-v2` + évaluation, texte de l'onboarding | non, sauf si la migration touche le résultat stocké | revue linguistique du jeu **[D3]** |
| 3 | **Banque d'exercices de langue** | 1 et 2 (niveau et catégories) | table d'ajout seul, générateur, contrôles, résolution à l'aveugle, tirage, niveau de travail par catégorie, signalement, suite d'évaluation | **oui, recommandée** (migration en ajout seul, contenu servi à des apprenants) | modèles **[D5]**, échantillon **[D6]** |
| 4 | **Retrait du verrou de 14 jours** | rien | section 6 | **oui, ciblée** | **[D7]** |

Ordre recommandé : **1 → 2 → 3**, la PR 4 à tout moment (je la ferais en dernier : elle est sans
effet utilisateur). La PR 1 passe en premier parce qu'elle ne demande ni contenu ni décision, qu'elle
exploite dès maintenant les séances réelles du frère d'Ismail, et que ses données (catégories qui
reviennent le plus) disent quels exercices générer en priorité. Les PR 1 et 2 peuvent avancer en
parallèle (fichiers différents) ; la 3 attend les deux.

Hors périmètre, volontairement : rubriques et score FSP, `arztbrief`, orthographe, prononciation,
profils par Land, simulateur d'apprenants (action 9), suivi des apprenants (action 14).

## 8. Décisions du propriétaire

**Tranchées le 2 novembre 2026 (Ismail) :** D1, D2, D4 à D8 acceptées telles que recommandées.
**D3 modifiée** : pas de relecteur humain germanophone ; la revue linguistique du jeu de test de
niveau (et la relecture des exercices générés, D6) est faite par **un juge LLM aux consignes
strictes**, d'un autre modèle que l'auteur, avec une grille écrite et versionnée, un verdict par
item stocké comme revue (`reviewer_name` = modèle et version de la grille), et la résolution à
l'aveugle de chaque item. Le bouton « Signaler une erreur » vaut aussi pour le test de niveau. Le
tirage humain de 10 % devient facultatif. **D9** (jeu allemand publié sur le serveur ?) reste à
vérifier. Stockage des exercices : base `platform`, voir la section 5.

| # | Décision | Ma recommandation |
|---|---|---|
| **D1** | Durée cible du test de niveau : garder ~15 min, ou viser plus court ? | ~10 min : 12 QCM au plus, 2 écoutes, 2 prises de parole. Garder les prises de parole : c'est ce qui donne les points faibles les plus parlants. |
| **D2** | Test obligatoire avant la première séance, ou « plus tard » possible ? | Garder « plus tard » (décision M3). Le programme n'ouvre déjà aucune séance vocale sans niveau ; on ajoute un rappel avant la première séance, pas de verrou dur. |
| **D3** | Qui relit le jeu allemand du test de niveau (revue linguistique du hash) ? | Une personne germanophone formée (enseignant d'allemand ou natif), pas le frère médecin. À nommer. |
| **D4** | Échelle B1–C1 avec sonde A2 « sous B1 » (message `prerequis` existant) ? | Oui. |
| **D5** | Modèles du générateur et du contrôle à l'aveugle (section 6 du plan : changement de modèle en production) | Générateur : `gpt-6-luna` (aligné sur les décisions du 15 octobre). Contrôle à l'aveugle : un modèle d'une autre famille, par exemple Claude Sonnet 5.5, par lots hors ligne (coût faible, pas de plafond en euros). |
| **D6** | Profondeur de la relecture humaine des exercices générés | Lecture complète des 2 premiers lots (environ 100 items) par la personne de **D3**, puis 10 % d'échantillon et le bouton « Signaler ». Pas de relecture médicale : décor seulement. |
| **D7** | Retrait du verrou de 14 jours maintenant, ou dans l'action 7 ? Revue Opus ? | Le faire, en dernier, avec une revue Opus ciblée (diff petit). Le laisser dans l'action 7 économiserait une revue mais laisserait une règle jugée fausse dans la doc. |
| **D8** | Niveau de travail par catégorie, sans écraser `estimated_level` | Oui (section 4.4). |
| **D9** | Y a-t-il un jeu de test de niveau allemand **déjà publié** sur le serveur ? | À vérifier avant le test du frère : sinon l'apprenant voit « Aucun test de niveau publié ». |

## 9. Risques

- **Allemand faux enseigné** : contrôles mécaniques, résolution à l'aveugle, relecture, signalement
  (section 5).
- **Corrections de l'évaluateur non mesurées** : « Votre phrase » reconnaît seulement une
  correspondance exacte et ne note jamais un échec sur « non reconnu ».
- **Estimation fragile** (8 items par niveau, escalier court) : acceptée, le niveau de travail par
  catégorie se corrige avec l'usage, et le test peut être refait.
- **Coût** : l'oral du test ne coûte pas un appel de plus. La banque se génère hors ligne.
  L'évaluation passe déjà à ×3 (vote sur 3 passes) : aucun changement ici.
- **Dérive de périmètre** : la remédiation ne dépend pas de l'action 7 (taxonomie FSP) ; elle utilise
  les catégories fermées déjà mesurées par `evaluation-language`.
