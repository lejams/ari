# Back-office : relecture et validation des protocoles

Application séparée de l'app apprenant (`ari.backoffice`, port 8100, pages dans
`backoffice-web/`). Elle seule voit les PDF bruts et la base `content`. Interface en français.

## Comptes et rôles

Trois rôles, cumulables : `owner` (propriétaire : dépôt des PDF, validation gold, comptes),
`physician_reviewer` (relecteur médecin : corrige et approuve les protocoles),
`linguistic_reviewer` (relecteur linguistique, utilisé à l'étape 4 pour les bundles).

Un compte naît par invitation : le propriétaire le crée (page Comptes, ou
`python -m ari.backoffice_api.cli create-owner --email … --name …` pour le tout premier) et
obtient un lien `#/invitation/<jeton>` valable 48 h, à transmettre par un canal sûr. La personne
y choisit son mot de passe (12 caractères au moins, haché argon2) et est connectée. Un nouveau
lien sert de réinitialisation et déconnecte partout. Les sessions sont des jetons serveur
aléatoires (cookie `ari_backoffice`, httpOnly, SameSite strict, `Secure` en production, 7 jours) ;
cinq échecs de connexion verrouillent le compte quinze minutes ; chaque création, invitation,
connexion échouée et désactivation est journalisée dans `backoffice_audit`, table append-only.
Désactiver un compte révoque ses sessions.

## Parcours

1. **Documents** (propriétaire). Déposer le PDF avec sa déclaration : Land, ville, Ärztekammer,
   mois, spécialité si connus ; droits (`compatible` avec preuve, sinon la publication restera
   bloquée) ; provenance et consentement en clair. Le traitement démarre en arrière-plan ; la
   page du document montre les tâches, les segments trouvés et leur protocole. Un protocole
   manqué se déclare à la main (première et dernière pages, premiers mots copiés exactement).
2. **Envoyer en relecture** (propriétaire), par document ou par protocole.
3. **Relecture** (médecin). La page d'un protocole met la page source à gauche (image, ou texte)
   et l'enregistrement structuré à droite : localisation, patient, anamnèse par section FSP,
   diagnostic, questions des examinateurs, Arzt-Arzt, Fachbegriffe, issue, questions de l'IA,
   incertitudes, pédagogie. La checklist « Points à résoudre » liste ce qui bloque l'approbation :
   toute incertitude non vidée, toute question critique sans réponse, la difficulté non
   renseignée. Le médecin corrige, **enregistre une version** (concurrence optimiste : si une
   version plus récente existe, la page demande de recharger), puis approuve, demande des
   corrections ou rejette, avec une note obligatoire hors approbation.
4. **Validation** (propriétaire). Sur un protocole approuvé : différences avec la version
   précédente, revues, détections PII masquées. Valider exige un Land, aucune détection PII ou
   une note de dérogation, des droits non incompatibles ; le protocole devient **gold**, figé par
   hash. Renvoyer au médecin ou rejeter sont les autres issues.
5. **Gold** : liste par Land, export JSONL réservé au propriétaire. La page d'un protocole gold
   permet au propriétaire de **générer des cas d'entraînement** : phases (Arzt-Patient,
   Arzt-Arzt, Fachbegriffe, selon ce que le protocole permet), tempérament du patient simulé,
   niveau de langue, numéro de révision. Le worker produit un brouillon de bundle ; la page
   liste les générations en cours et les brouillons (valides ou invalides avec leurs raisons).
   **Importer dans le registre** copie un brouillon valide dans le registre plateforme.
6. **Registre** : les scénarios importés, par statut. La page d'un scénario montre les blocages
   de publication, les revues, le bundle complet DE/FR, un formulaire de revue (clinique pour
   un relecteur médecin, linguistique pour un relecteur linguistique, notes obligatoires) et,
   pour le propriétaire, **Publier aux apprenants** (actif quand la liste des blocages est
   vide) ou **Retirer**. Deux approbations par deux comptes distincts sur le contenu exact sont
   nécessaires ; le même compte ne peut pas donner les deux.

Ce que le serveur refuse quoi qu'affiche l'interface : approuver avec des points ouverts,
décider sur une version qui n'est plus la courante, modifier un protocole gold, rejeté ou
remplacé, valider sans Land, importer un brouillon invalide ou dont le cas ne trace pas vers
le protocole gold figé, publier avec un blocage, donner les deux approbations depuis un même
compte.

## Sécurité

PDF et images de pages servis uniquement à un compte connecté, en `Content-Disposition:
attachment`, `nosniff`, `Cache-Control: private, no-store` ; fichiers adressés par SHA-256, jamais
par nom ; plafond de 50 Mo vérifié en streaming et en-tête `%PDF` exigé ; mutations refusées
depuis une autre origine que celle du back-office ; erreurs de validation renvoyées sans
l'entrée ; l'app apprenant n'a pas les identifiants de la base `content`.

## Déploiement sur un serveur

Un seul serveur, docker compose, deux noms DNS (app apprenant, back-office) pointant dessus. Le
back-office reçoit les identifiants des deux bases : `content` pour les protocoles, `platform`
pour importer et publier les cas dans le registre que lit l'app apprenant.

```sh
cp deploy/env.example .env          # puis : mots de passe Postgres, clé OpenAI, ARI_PROVIDER_MODE=openai,
                                    # ARI_DOMAIN=app.exemple.org, ARI_BACKOFFICE_DOMAIN=admin.exemple.org
docker compose --profile serve up -d --build
docker compose --profile serve run --rm backoffice \
  python -m ari.backoffice_api.cli create-owner --email vous@exemple.org --name "Votre nom"
```

Le service `migrate` applique les deux chaînes Alembic avant que `app`, `backoffice` et `worker`
démarrent ; `proxy` (Caddy) obtient les certificats et met le back-office derrière HTTPS, ce que
les cookies `Secure` de la production exigent. Les PDF vivent dans le volume `content_storage`,
les bases dans `postgres_data` : à sauvegarder ensemble. Le lien d'invitation imprimé par
`create-owner` s'ouvre sur `https://admin.exemple.org/#/invitation/…`.

En local, `make backoffice` sert le back-office sur <http://localhost:8100> avec rechargement,
`make worker` lance le worker ; le premier compte se crée avec la même commande `create-owner`
(`PYTHONPATH=backend/src .venv/bin/python -m ari.backoffice_api.cli …`).
