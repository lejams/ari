# Contrats manquants et proposition de sécurité

## Modification autorisée : projection publique des sessions

Problème vérifié : `api/app.py::session_payload` sérialise actuellement tout le domaine. Les événements WebSocket sérialisent aussi `ConversationTurn` en entier. Cela expose `selected_fact_ids`, `revealed_fact_ids`, `canonical_response`, les audits de grounding et les exécutions internes au navigateur.

Autorisation utilisateur reçue le 9 septembre 2026. Implémentation : une projection publique explicite des sessions et tours pour HTTP et WebSocket. Conserver les identifiants de session/tour, les textes réellement échangés, états et corrélations audio indispensables, horodatages, mode, configuration publique du transport et feedback post-session. Exclure les identifiants de faits, réponses canoniques internes, audits, exécutions, configurations internes et clés d’idempotence. Conserver toutes les données en stockage serveur. Aucun changement de schéma, aucune migration, aucune modification du moteur clinique. Ajouter des tests HTTP/WebSocket anti-fuite et vérifier la reprise audio.

## Capacités restant à définir, non implémentées

- Profil enrichi : endpoint authentifié GET/PATCH avec objectif FSP/KP/prise de poste, date nullable, Land, minutes/jour, situation, spécialité, niveau déclaré et source, étape d’onboarding ; validation serveur et stockage durable avec migration additive à proposer séparément.
- Justificatif : stockage privé, limites MIME/taille, endpoint de dépôt lié au propriétaire et états `uploaded`, `pending_review`, `verified`, `rejected`. Seul un processus de vérification autorisé peut attribuer `verified` ; un fichier sélectionné ou déposé ne suffit pas.
- Positionnement : consultation/version/rubrique dédiées, sortie structurée fondée sur transcript, méthode de calibration explicitement validée. Le moteur actuel de feedback n’est pas une mesure CEFR.
- Programme calibré : contrat de recommandation lié au positionnement ou au niveau vérifié ; aujourd’hui seule une sélection expliquée de contenus disponibles est possible.
- Carnet de vocabulaire personnel : liste, ajout depuis preuves du feedback et état de révision persistant. Les aides par session existent mais ne constituent pas ce carnet.

Aucune de ces capacités ne doit être simulée silencieusement par le frontend.
