# Relecture du jeu `ari-placement-de@2` par le juge LLM

- Jeu : `cases/placement/ari_placement_de.v2.yaml`, hash `241e17293006b44c0af507bcb975fe2557f53e3d207dd22fea36bf9ce91c84cd`.
- Juge : claude-opus-5-5 (les items ont été écrits par un autre modèle, Sonnet 5.5). Grille : `placement-grid-v1`
  (`docs/PLACEMENT_REVIEW.md`). Décision du propriétaire du 2 novembre 2026 (D3) : un juge LLM aux
  consignes strictes, pas une personne.
- Procédure : résolution à l'aveugle (sans clé, niveau ni catégorie), puis relecture avec la clé. Trois
  tours de corrections ont précédé ce passage final (environ vingt items réécrits, surtout des
  distracteurs défendables et l'écoute `l-c1-1`, qui restait devinable sans écoute ou incohérente).
  La résolution à l'aveugle de la version finale de `l-c1-1` trouve la clé mais conclut qu'on ne peut pas
  y répondre sans l'écoute.
- Verdict global : **approve** (41 sur 41 `ok`).

## Limites

- Relecture par un modèle : elle ne remplace pas une personne de langue allemande. Les signalements
  d'erreurs des apprenants (`item_report`) servent de filet après publication.
- 7 écarts de niveau d'un cran, non bloquants : B1 un peu facile (b1-1, b1-2, b1-7) et C1 un peu
  facile (c1-1, c1-2, c1-8, l-c1-2). Le test reste monotone, l'escalier converge un cran plus bas sur
  ces points seulement. À recaler quand des réponses réelles existeront.
- Remarques mineures : la tournure « Begründen Sie, was » de `s-1` est un peu maladroite ; `l-c1-2` peut
  en partie se deviner sans l'écoute.
- Aucun étalonnage psychométrique : le résultat est une estimation, jamais un certificat.

## Verdicts

| Item | Verdict | Catégorie juste | Niveau | Raison |
|---|---|---|---|---|
| a2-1 | ok | oui | oui | Datif imposé par geben (Kind neutre : dem), « das » et « den » fautifs ; allemand correct. |
| a2-2 | ok | oui | oui | Praxis est féminin, seule « die » convient ; niveau plutôt A1 mais acceptable comme plancher du test. |
| a2-3 | ok | oui | oui | Participe passé de gehen avec sein, « gegehen » et l'infinitif sont fautifs. |
| a2-4 | ok | oui | oui | Inversion verbe-sujet après adverbe en tête, les deux autres options gardent le verbe en troisième position ; le distracteur avec « nicht » ajoute du sens mais reste faux par l'ordre. |
| a2-5 | ok | oui | oui | Seul « Tablette » a du sens, Tasche et Tafel sont des paronymes sans rapport. |
| a2-6 | ok | oui | oui | Le contexte (médecin, « höflich ») impose le Sie de politesse, du et ihr exclus. |
| a2-7 | ok | oui | oui | warten auf + accusatif masculin : den, seule réponse correcte. |
| a2-8 | ok | oui | oui | Seul « aufstehen » est correct ; anziehen exigerait un complément ou un réfléchi, einsteigen n'a pas de sens ici. |
| b1-1 | ok | oui | un cran trop haut (plutôt A2) | Allergie féminin, accusatif « eine » seul correct ; l'accord de l'article indéfini relève plutôt de l'A2. |
| b1-2 | ok | oui | un cran trop haut (plutôt A2) | helfen régit le datif, « mir » seul correct ; point plutôt A2 (écart d'un cran). |
| b1-3 | ok | oui | oui | Passif avec verbe de modalité, participe « genommen » seul correct. |
| b1-4 | ok | oui | oui | Interrogative indirecte avec verbe en fin de proposition, les deux autres ordres sont fautifs. |
| b1-5 | ok | oui | oui | sich kümmern um, für et über sont exclus en allemand standard. |
| b1-6 | ok | oui | oui | « gestern » impose le passif au prétérit « wurde » ; « wird » est incompatible et « worden » exige un auxiliaire. |
| b1-7 | ok | oui | un cran trop haut (plutôt A2) | mit + datif féminin : der, seule réponse correcte ; point typiquement A2. |
| b1-8 | ok | oui | oui | Seule « Mit freundlichen Grüßen » convient à une lettre formelle, les deux autres sont familières. |
| b2-1 | ok | oui | oui | Génitif relatif accordé à l'antécédent neutre (dessen) ; deren (féminin ou pluriel) et denen (datif pluriel) sont faux. |
| b2-2 | ok | oui | oui | bei + datif féminin dans la relative : der, seule correcte ; allemand correct. |
| b2-3 | ok | oui | oui | aufgrund régit le génitif : des Notfalls, « dem » n'est pas standard à l'écrit. |
| b2-4 | ok | oui | oui | Irréel du passé, la principale exige le subjonctif II « wäre » ; bin et war sont exclus. |
| b2-5 | ok | oui | oui | Infinitif passé « genommen zu haben », seule forme correcte. |
| b2-6 | ok | oui | oui | Subordonnée en dass : bloc verbal « gebracht worden ist » en fin, les deux autres ordres sont fautifs. |
| b2-7 | ok | oui | oui | « gut verträglich » seul correct ; « ist gut vertragen » n'est pas idiomatique et « vertraglich » signifie contractuel. |
| b2-8 | ok | oui | oui | « klagt über » est la formule du registre médical écrit, jammern et motzen sont familiers. |
| c1-1 | ok | oui | un cran trop haut (plutôt B2) | Antécédent féminin : deren ; derer n'est pas admis comme relatif suivi d'un nom (Duden) ; même structure que b2-1, plutôt B2. |
| c1-2 | ok | oui | un cran trop haut (plutôt B2) | Déclinaison mixte au génitif neutre : aktuellen seul correct ; la déclinaison de l'adjectif relève plutôt du B2. |
| c1-3 | ok | oui | oui | bedürfen régit le génitif : des, seule correcte ; « eines » serait plus naturel mais ne figure pas parmi les options. |
| c1-4 | ok | oui | oui | Passif modal au subjonctif II passé « hätte vermieden werden können », seul « werden » est correct. |
| c1-5 | ok | oui | oui | Participe II attributif « eingelieferte » ; le participe I donnerait un sens actif absurde et « einlieferte » n'existe pas. |
| c1-6 | ok | oui | oui | Discours indirect au subjonctif I avec double infinitif : « nicht machen lassen wollen » en fin, les deux autres ordres sont fautifs. |
| c1-7 | ok | oui | oui | « zusehends verschlimmert » seul correct, zuhauf et zuwider ne conviennent ni par le sens ni par l'emploi. |
| c1-8 | ok | oui | un cran trop haut (plutôt B2) | « erhielten » est le seul verbe du registre scientifique, kriegten est familier et schnappten faux ; la distinction kriegen/erhalten relève plutôt du B2. |
| l-a2-1 | ok | oui | oui | Les trois horaires sont plausibles sans écoute, le script donne clairement « Dienstag um halb elf ». |
| l-b1-1 | ok | oui | oui | Le script dit « getrunken nur einen Tee », réponse non déductible de la question, distracteurs plausibles. |
| l-b1-2 | ok | oui | oui | Condition explicite dans le script, le distracteur « nach dem Essen » reprend un mot entendu, bonne écoute. |
| l-b2-1 | ok | oui | oui | Proposition explicite d'une nouvelle mesure dans trois mois, distracteurs exclus seulement à l'écoute ; contenu à la limite B1/B2. |
| l-b2-2 | ok | oui | oui | La cause (« Gerät gewartet ») n'est donnée que par le script, script cohérent et correct. |
| l-c1-1 | ok | oui | oui | Réponse paraphrasée (Zeit stehlen / Zeit nehmen) et distracteur explicitement nié par « nicht die Schwere der Symptome », vraie compréhension C1. |
| l-c1-2 | ok | oui | un cran trop haut (plutôt B2) | Une seule phrase assez simple, « Nebenwirkungen » est en partie devinable sans écoute ; niveau plutôt B2. |
| s-1 | ok | oui | oui | Trois tâches B1 claires et faisables en une minute ; « Begründen Sie, was ... » est un peu maladroit (« Sagen Sie, was ... und warum » serait plus idiomatique) sans nuire à la clarté. |
| s-2 | ok | oui | oui | Consigne claire (expliquer et convaincre poliment), faisable en une minute, niveau B2 plausible. |
