# Déploiement et exploitation

Un seul environnement privé, traité comme la production, sur un VPS avec Docker Compose et Caddy.
Il héberge l'app apprenant, le back-office, le worker, deux bases PostgreSQL et un proxy HTTPS.
La branche `main` est déployée dessus. Objectif : le relecteur distant produit le premier corpus
allemand dans le back-office, et l'environnement est restaurable depuis une sauvegarde testée.

## 1. Cible

- VPS 2 vCPU / 4 Go RAM / 40 Go SSD minimum (8 Go de RAM plus confortables pour le worker PDF).
- Ubuntu 24.04 LTS, architecture amd64/x86_64 pour rester aligné avec la CI.
- Docker Engine + plugin compose >= 2.20 ; installer aussi `age`, `rclone`, `git`, `curl`,
  `openssl`, `ufw` et `unattended-upgrades`.

## 2. Préparation du serveur (une fois)

Commandes de référence, pas un script :

```sh
# Compte d'administration et compte de déploiement séparés.
adduser admin && usermod -aG sudo admin
adduser ari
# Après l'installation de Docker, donner à ari l'accès Docker (pas sudo).
usermod -aG docker ari

# Pare-feu : seuls SSH, HTTP et HTTPS. PostgreSQL n'est jamais exposé.
ufw default deny incoming && ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable

# SSH par clés uniquement.
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

# Mises à jour de sécurité automatiques (avec fenêtre de redémarrage).
apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades
```

La rotation des logs Docker est gérée dans `docker-compose.yml` (json-file, 20 Mo x 5 par service).
Après l'installation et le premier déploiement, redémarrer une fois le serveur pour vérifier que le
pare-feu et les mises à jour survivent au reboot, puis relancer explicitement
`docker compose --profile serve up -d --wait` : les conditions `depends_on` ne sont évaluées que
par `compose up`, pas par un redémarrage automatique du démon Docker.

## 3. DNS

Deux enregistrements A (et AAAA en IPv6) vers l'IP du VPS : le domaine apprenant et le domaine
back-office. Attendre la propagation avant le premier `up` (Let's Encrypt limite les tentatives).

E-mails des comptes apprenants (activation, mot de passe oublié) : déclarer le domaine de
l'adresse d'expédition (par exemple `no-reply@<domaine>`) chez le fournisseur SMTP, puis ajouter
les enregistrements qu'il fournit. Sans eux, les liens finissent en spam ou sont refusés :

- **SPF** (TXT) : autorise le fournisseur à envoyer pour le domaine ;
- **DKIM** (TXT ou CNAME) : signe les messages ;
- **DMARC** (TXT sur `_dmarc`) : commencer par `v=DMARC1; p=none`, durcir une fois les envois stables.

## 4. Bucket de sauvegarde

- Créer un bucket S3-compatible (Cloudflare R2 ou Backblaze B2) et une clé restreinte en lecture
  et écriture d'objets sur ce seul bucket.
- Trois règles de cycle de vie sur les préfixes : `daily/` 15 jours, `weekly/` 60 jours,
  `monthly/` 400 jours. La rétention est donc gérée par le bucket, pas par le script.
- Configurer le remote rclone (nom `ari-backup`) : `rclone config`.
- Générer une paire de clés `age` : `age-keygen -o ari-backup.key`. La clé publique va dans `.env`
  (`BACKUP_AGE_RECIPIENT`), la clé privée reste dans le gestionnaire de mots de passe, jamais sur le VPS.

## 5. Secrets

- Trois mots de passe PostgreSQL : `openssl rand -hex 24` pour chacun.
- Hash basic auth apprenant : `docker run --rm caddy:2.10-alpine caddy hash-password --plaintext '<mot de passe>'`.
  Le coller entre quotes simples dans `.env` (le hash contient des `$`).
- Clé du projet OpenAI, avec un **plafond de dépense mensuel dur** défini dans le tableau de bord
  OpenAI. C'est la limite de coût de l'alpha (B01) ; il n'y a pas encore de quota par utilisateur.
- Relais SMTP transactionnel pour les e-mails des comptes (Brevo, Scaleway Transactional Email,
  Postmark, Amazon SES...). Accepter le contrat de sous-traitance (DPA) du fournisseur : il traite
  les adresses des apprenants. Dans `.env` :

  ```sh
  ARI_EMAIL_MODE=smtp
  ARI_EMAIL_FROM='ARI <no-reply@<domaine>>'   # une adresse du domaine déclaré au §3
  ARI_SMTP_HOST=<hôte fourni>
  ARI_SMTP_PORT=587                           # 587 + starttls ; le 25 et souvent le 465 sont bloqués
  ARI_SMTP_USERNAME=<identifiant fourni>
  ARI_SMTP_PASSWORD='<clé SMTP>'              # quotes simples : une clé peut contenir des `$`
  ARI_SMTP_SECURITY=starttls
  ```

  `ARI_PUBLIC_URL` (l'adresse des liens envoyés) est dérivée de `ARI_DOMAIN` par le compose. Tant
  que `ARI_EMAIL_MODE=log`, les liens ne partent pas : ils sont écrits dans les logs de `app`, et
  un avertissement le signale au démarrage.

## 6. Premier déploiement

```sh
sudo mkdir -p /opt/ari && sudo chown ari:ari /opt/ari
git clone https://github.com/lejams/ari.git /opt/ari && cd /opt/ari
git checkout main
cp deploy/env.example .env    # renseigner mots de passe, domaines, basic auth, clés OpenAI, ARI_PROVIDER_MODE=openai, ARI_ENVIRONMENT=production
chmod 600 .env
deploy/deploy.sh
```

Sur une installation neuve, `deploy.sh` détecte l'absence des deux schémas Alembic et affiche
qu'il saute la sauvegarde pré-déploiement ; il ne faut pas démarrer PostgreSQL manuellement avant
ce premier appel. Lors des déploiements suivants, une sauvegarde `predeploy-<sha>` est obligatoire.

Attendu : `migrate` sort en succès, les services applicatifs et PostgreSQL sont `healthy`, deux
certificats Let's Encrypt sont obtenus.
Créer le premier compte propriétaire du back-office et l'inviter :

```sh
docker compose --profile serve run --rm backoffice \
  python -m ari.backoffice_api.cli create-owner --email vous@exemple.org --name "Votre nom"
```

Ouvrir le lien d'invitation imprimé (`https://<domaine back-office>/#/invitation/…`).

## 7. Vérifications de mise en service

Dans l'ordre :

1. `curl -u ari:<mdp> https://<domaine apprenant>/api/health` et `curl https://<domaine back-office>/api/health`.
   La landing est publique, le reste du domaine apprenant non : `curl -o /dev/null -w '%{http_code}'`
   doit renvoyer `200` sur `https://<domaine apprenant>/` et `401` sur `/app` et `/api/health` sans `-u`.
2. Connexion back-office, upload d'un PDF, suivi des jobs (`docker compose logs -f worker`), publication d'un cas.
3. Comptes apprenants : sur `https://<domaine apprenant>/connexion`, créer un compte avec une
   adresse à vous. Le lien d'activation doit arriver en boîte de réception (pas en spam) ; sinon
   `docker compose logs app | grep "not delivered"`. Un envoi vers mail-tester.com doit obtenir 9/10
   ou plus (SPF, DKIM et DMARC valides). Tester aussi « Mot de passe oublié ».
4. Depuis l'app apprenant (`/app`, derrière basic auth), lancer une session et un appel vocal complet.
5. Interrupteur : `ARI_SERVICE_PAUSED=true` dans `.env`, `docker compose --profile serve up -d app`,
   vérifier le message de pause sur une nouvelle session et que l'historique reste consultable, puis remettre à `false`.

## 8. Sauvegardes

- Configurer `rclone` sous l'utilisateur `ari`, avec le remote `ari-backup`, puis préparer les
  chemins avec les bonnes permissions :

  ```sh
  sudo install -d -o ari -g ari -m 700 /var/backups/ari
  sudo install -o ari -g ari -m 600 /dev/null /var/log/ari-backup.log
  ```

  Installer ensuite le cron sous `ari` avec un `PATH` explicite :

  ```cron
  SHELL=/bin/bash
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  0 3 * * * cd /opt/ari && ./deploy/backup.sh >> /var/log/ari-backup.log 2>&1
  ```
- Un check healthchecks.io (`BACKUP_HEALTHCHECK_URL`) avec une grâce de 26 h alerte si le job ne
  ping pas. Le script ping aussi `<url>/fail` en cas d'échec.
- Un jeu contient : dump des deux bases, rôles globaux, archive du volume `content_storage`,
  le tout chiffré `age` en un seul fichier `.tar.age`.

## 9. Exercice de restauration (mensuel)

Une sauvegarde jamais restaurée ne vaut rien. Une fois par mois, sur le VPS ou une machine jetable :

```sh
rclone copy ari-backup:ari-backups/daily/<jeu>.tar.age .
AGE_IDENTITY_FILE=~/ari-backup.key deploy/restore.sh <jeu>.tar.age
# vérifier les comptes affichés, puis :
docker compose -p ari-restore down -v
```

Supprimer la clé privée après l'exercice. Documenter le temps réel nécessaire pour repartir.

## 10. Mise à jour et retour arrière

- Mise à jour : `deploy/deploy.sh` est le seul chemin (arbre propre, branche `main`). Il prend une
  sauvegarde `predeploy-<sha>` avant toute migration.
- Retour arrière du code : `ARI_IMAGE_TAG=<sha précédent> docker compose --profile serve up -d --no-build`.
- Retour arrière des données (si une migration a tourné) : restaurer le jeu `predeploy-<sha>`.
  Ne jamais faire `alembic downgrade` en production.
- Ne jamais lancer `docker compose down -v` sur le projet live : cela détruit les volumes.

## 11. Fenêtre de déploiement

L'état d'une connexion vocale est en mémoire et l'app tourne sur une seule instance. Tout
redémarrage de `app` (déploiement, bascule de l'interrupteur) coupe les sessions vocales en cours.
Déployer quand le relecteur confirme qu'aucun apprenant n'est connecté. À revoir avant l'alpha ouverte (B08).

## Références

Repo : github.com/lejams/ari. Documentation liée : `docs/BACKOFFICE.md`, `docs/CONTENT_PIPELINE.md`,
`docs/MIGRATIONS.md`, `docker-compose.yml`, `deploy/Caddyfile`, `deploy/env.example`.
