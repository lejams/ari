# Calibrage du test de niveau sur des épreuves modèles publiques

Notes de calibrage uniquement : formats, longueurs, difficulté. **Aucune question, aucun texte ni
aucune consigne d'une épreuve n'entre dans le dépôt** (contenus protégés). Les documents consultés
restent hors du dépôt. Le propriétaire a confirmé le 2 novembre 2026 qu'on peut s'en inspirer, sans
copier, pour tous les niveaux (Goethe B1, B2, C1 ; telc B1-B2 Pflege ; telc C1 Medizin), avec les
descripteurs du CECRL comme référence officielle.

## 1. Ce qui a été lu

Les sujets modèles du Goethe-Zertifikat B1, modules **Lesen** et **Schreiben**, passés par le frère
du propriétaire dans un organisme de cours particuliers.

| Module | Format observé | Durée |
|---|---|---|
| Lesen | 5 parties : courriel personnel (vrai ou faux, 6 énoncés), deux articles de presse (3 énoncés à 3 choix chacun), 7 situations à associer à 10 annonces, 7 opinions courtes (pour ou contre), un texte de règles (4 énoncés à 3 choix) | 65 min |
| Schreiben | 3 productions : courriel à un ami (environ 80 mots, trois points de contenu imposés : décrire, justifier, proposer), contribution à une discussion en ligne (environ 80 mots), courriel formel d'excuse (environ 40 mots) | 60 min |

## 2. Ce que cela dit de la difficulté B1

- Les textes de lecture sont du français courant transposé : phrases de 10 à 20 mots, `weil`, `dass`,
  `wenn`, `obwohl`, parfait, `hätte` pour l'irréel, comparatifs. Les items B1 du jeu (`b1-1` à `b1-8`)
  restent dans cette zone ; le juge LLM en a signalé quatre comme proches de A2 (un cran), ce qui va
  dans le sens d'un test qui ne surestime pas le B1.
- L'épreuve **ne teste pas la grammaire isolée** : elle teste la compréhension et la production.
  Notre QCM à trou est un indicateur indirect, utile pour localiser des catégories d'erreur (c'est
  son rôle ici), pas un équivalent de l'épreuve.
- Les consignes de production donnent **trois points de contenu** à traiter. Adopté (format
  seulement) pour la consigne orale `s-1` du jeu allemand : décrire, justifier, proposer.
- Les critères de correction publiés du Goethe-Institut pour l'écrit sont l'adéquation à la tâche, la
  cohérence, le vocabulaire et les structures. Ils recoupent déjà ce que demande le prompt
  `placement-speaking-v2` (étendue du vocabulaire, maîtrise grammaticale, complexité, cohésion,
  réalisation de la tâche).

## 3. Ce que cela change pour ce lot

- Aucun changement de seuil de gate : ce document ne donne aucune mesure, seulement un ordre de
  grandeur de difficulté.
- La consigne orale `s-1` suit désormais le modèle « trois points de contenu ».
- Les niveaux B2 et C1 du jeu restent calibrés sur les descripteurs du CECRL ; les épreuves B2 et C1
  citées plus haut ne sont pas encore lues.

## 4. Suite possible (hors de ce lot)

- **Une production écrite courte** (une quarantaine de mots, un point de contenu par consigne),
  notée par le même évaluateur avec les quatre critères. Elle donne des points faibles par catégorie
  sans l'erreur de transcription de la voix, et correspond mieux à l'épreuve réelle. Elle demande un
  nouveau prompt, donc une mesure `ari.evals`.
- **Une courte compréhension de l'écrit** (un texte de 100 à 150 mots, trois énoncés vrais ou faux ou
  à choix) pour le niveau de lecture, que l'épreuve réelle pèse autant que les autres.
- Lire les sujets B2 (Goethe) et C1 (Goethe, telc Medizin) pour caler les items `b2-*` et `c1-*`,
  les plus exposés à un écart de niveau d'un cran dans la relecture.
