# Relecture du jeu de test de niveau (grille `placement-grid-v1`)

Décision du propriétaire (2 novembre 2026, D3) : le jeu de test de niveau n'est pas relu par une
personne mais par **un juge LLM aux consignes strictes**, d'un autre modèle que celui qui a écrit les
items. Cette page est la grille. Elle est versionnée : toute modification crée `placement-grid-v2`.

Ce que la relecture garantit : que **chaque item a une réponse correcte et une seule**, que
l'allemand de l'énoncé est correct, et que l'item mesure bien sa catégorie et son niveau. Ce qu'elle ne
garantit pas : un étalonnage psychométrique (le test donne une estimation, jamais un certificat).

## 1. Deux passes, dans cet ordre

1. **Résolution à l'aveugle.** Le juge reçoit chaque item **sans la clé, ni le niveau, ni la catégorie**
   (énoncé et options pour un QCM ; transcription entendue, question et options pour une écoute). Il
   répond avec un seul indice, et dit s'il voit une autre option défendable.
2. **Relecture avec la clé.** Le juge reçoit ensuite la clé, le niveau et la catégorie et applique la
   grille ci-dessous.

Le code, lui, contrôle d'abord ce qui se contrôle sans modèle (`PlacementSetVersion`, schéma v2) :
exactement un trou `___` par énoncé, options distinctes, une catégorie par QCM, 8 QCM par niveau de
A2 à C1, 4 QCM par catégorie, au moins 2 écoutes par niveau de B1 à C1.

## 2. Grille (un verdict par item : `ok`, `corriger` ou `rejeter`)

Un item est **`ok`** seulement si les six points sont vrais.

1. **Une seule réponse correcte.** La clé est correcte en allemand standard écrit. Aucune autre
   option n'est correcte ni défendable dans le registre indiqué par l'énoncé. La résolution à
   l'aveugle a trouvé la clé et aucune autre option.
2. **Allemand correct.** L'énoncé, les options et la transcription d'une écoute sont de l'allemand
   correct (orthographe, ponctuation, grammaire), sauf la partie volontairement fausse d'une option.
3. **Catégorie exacte.** L'item mesure bien la catégorie annoncée : `gender` (accord en genre),
   `case` (cas), `verb_form` (forme verbale), `word_order` (ordre des mots), `word_choice` (choix du
   mot ou de la préposition), `register` (niveau de langue). Quand une option fausse relève d'une
   autre catégorie, la catégorie annoncée reste celle de la clé.
4. **Niveau plausible.** Le niveau CECRL annoncé est plausible pour ce qui est testé (descripteurs
   publics). Un écart d'un niveau est signalé, deux niveaux d'écart est `corriger`.
5. **Aucun savoir médical requis.** Le décor médical ne demande aucune connaissance clinique pour
   répondre. Une écoute ne se résout pas sans l'entendre (l'énoncé de la question ne donne pas la
   réponse).
6. **Neutralité.** Pas de coordonnées personnelles, pas de contenu discriminatoire, pas de piège
   culturel ou de jeu de mots qui avantage un natif sans rapport avec la langue testée.

Pour une consigne orale : la consigne est claire, réalisable en environ une minute, et son niveau
indicatif est plausible.

## 3. Verdicts conservés

Le verdict de chaque item (avec la raison en une phrase) est conservé dans
`docs/reviews/placement-de-v2-juge.md`, et la décision globale est enregistrée comme revue de registre
(`PlacementReview`, `reviewer_name` = modèle du juge et version de la grille) sur le **hash exact** du
jeu : `cases/placement/ari_placement_de.v2.review.yaml`. Tout changement d'un seul caractère du jeu
change le hash et exige une nouvelle relecture.

## 4. Filets après publication

- **Bouton « Signaler une erreur dans cette question »** pendant le test : l'événement `item_report`
  est enregistré dans la piste d'audit du jeu (`docs/CLINICAL_CASES.md`). Le propriétaire le relit ; si le
  défaut est confirmé, c'est une nouvelle version du jeu, relue de la même façon.
- Un tirage humain facultatif (10 %) reste possible à tout moment : c'est un complément, pas un
  prérequis.

## 5. Limites connues de cette relecture

- Le juge est un modèle : il peut partager des angles morts avec l'auteur (autre modèle, mais même
  famille de données). La résolution à l'aveugle en réduit le risque sans le supprimer, d'où le bouton
  de signalement.
- Le niveau CECRL d'un item est un jugement, pas une mesure. Les écarts d'un cran sont signalés et
  conservés dans le rapport de relecture ; aucune calibration psychométrique n'a été faite.
- Une écoute est jugée « devinable sans l'entendre » par un lecteur, pas par un apprenant.
