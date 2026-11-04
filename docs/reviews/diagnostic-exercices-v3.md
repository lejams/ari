# Proposition pour les refus du holdout language-exercises@3

Le [run du 4 novembre](https://github.com/lejams/ari/actions/runs/41912108307) a refusé 17 passages sur 42 d'exercices étiquetés valides, tous au stade rubric_judge. Il a refusé tous les exercices défectueux lors des 336 passages. Trois références concentrent 9 des 17 refus. Les raisons individuelles du juge sont absentes de l'artefact. Les observations ci-dessous proviennent de la lecture des exercices et de la grille.

## Trois références de développement proposées

Conserver @3 tel que mesuré. Utiliser des identifiants distincts pour les propositions suivantes et les faire relire avant de les utiliser comme références. Le signe ✓ indique la clé.

| Source | Objectif | Énoncé | Options | Explication destinée à l’élève |
|---|---|---|---|---|
| k04 | B1, ordre des mots | Die Ärztin sagt, dass hat der Patient Fieber. | ✓ Die Ärztin sagt, dass der Patient Fieber hat. / Die Ärztin sagt, dass hat der Patient Fieber. / Die Ärztin sagt, dass der hat Patient Fieber. | Dans la subordonnée introduite par « dass », le groupe sujet « der Patient » reste entier et le verbe conjugué « hat » vient après « Fieber ». |
| k12 | Choix lexical, niveau à calibrer | Das Gutachten ___ den Verdacht ausdrücklich mit zwei voneinander unabhängigen Befunden. | ✓ untermauert / vertagt / unterzeichnet | « Einen Verdacht mit Befunden untermauern » signifie étayer un soupçon par des constatations. Les trois verbes ont la même forme grammaticale. |
| k13 | C1, ordre des mots | Der Patient bedauert, dass er die Behandlung nicht fortsetzen können hat. | ✓ Der Patient bedauert, dass er die Behandlung nicht hat fortsetzen können. / Der Patient bedauert, dass hat er die Behandlung nicht fortsetzen können. / Der Patient bedauert, dass er die Behandlung nicht fortsetzen können hat. | Dans cette subordonnée au parfait avec « können » et un infinitif, « hat » précède « fortsetzen können ». |

Pour k04, utiliser un ordre fautif sans dépendre de l’acceptabilité d’un complément placé après le verbe. Pour k12, choisir le verbe par le sens de la phrase. Pour k13, les options contiennent les mêmes mots et les mêmes formes. Avant incorporation, contrôler la difficulté et la plausibilité des distracteurs, notamment pour k04.

## Diagnostic ciblé à implémenter dans Codex

1. Comparer les trois références originales et les trois propositions dans un petit jeu de développement. Réutiliser le chemin de contrôle du générateur, avec la même configuration luna-sonnet et un passage par exercice. Enregistrer l’étape, les critères refusés, les raisons et le coût. En cas d’écart à la relecture, répéter uniquement l’exercice concerné. Les raisons observées seront celles du nouveau passage.
2. Si les trois cas sont compris, examiner k02, k03, k07, k10 et k14, responsables des huit autres refus. Vérifier chaque étiquette avant de modifier le juge. Garder e-h05 et les near_miss_key dans les régressions de développement.
3. Réserver le holdout suivant à la configuration figée et à des familles inédites relues. Le diagnostic doit produire un rapport de développement inutilisable pour autoriser un import. Tester cette séparation avec le fournisseur simulé avant tout appel facturé.

Décision proposée pour cette étape : conserver les seuils et le contrôle simple. Les modifications des références et tout changement ultérieur de grille seront évalués séparément.