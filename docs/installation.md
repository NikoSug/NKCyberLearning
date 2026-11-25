# Procédure d'installation de CyberLearning

*English version: [installation.en.md](installation.en.md)*

Ce document décrit l'installation complète d'une instance CyberLearning sur une infrastructure neuve, puis son
exploitation (mises à jour, sauvegardes, dépannage). Il s'adresse à un administrateur système à l'aise avec Linux
et Docker.

**Principe** : une instance = un client. Le même paquet s'installe **sur site** (serveur du client) ou **dans le
cloud** (machine virtuelle dédiée au client), avec la même procédure. Les données de chaque client sont isolées
par construction.

---

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Choisir et dimensionner la machine](#2-choisir-et-dimensionner-la-machine)
3. [Informations à réunir avant de commencer](#3-informations-à-réunir-avant-de-commencer)
4. [Préparer le serveur](#4-préparer-le-serveur)
5. [Récupérer l'application](#5-récupérer-lapplication)
6. [Lancer l'installation](#6-lancer-linstallation)
7. [Vérifier l'installation](#7-vérifier-linstallation)
8. [Premiers pas après l'installation](#8-premiers-pas-après-linstallation)
9. [Contenus : packs et mises à jour](#9-contenus--packs-et-mises-à-jour)
10. [Exploiter l'instance](#10-exploiter-linstance)
11. [Activer l'IA plus tard](#11-activer-lia-plus-tard)
12. [Dépannage](#12-dépannage)
13. [Désinstaller](#13-désinstaller)
14. [Annexes : réglages, fichiers, sécurité](#14-annexes)

---

## 1. Vue d'ensemble

```
                       Internet / réseau de l'entreprise
                                     │  ports 80 et 443
                              ┌──────▼──────┐
                              │    nginx    │  TLS, en-têtes de sécurité, limitation de débit
                              └──────┬──────┘
                    réseau interne « proxy » (sans accès Internet)
                              ┌──────▼──────┐
                              │     web     │  application Django (gunicorn)
                              └──────┬──────┘
                    réseau interne « backend » (sans accès Internet)
        ┌──────────────┬─────────────┼──────────────┬──────────────────────┐
   ┌────▼────┐    ┌────▼────┐   ┌────▼────┐   ┌─────▼─────┐   ┌────────────▼───────────┐
   │   db    │    │  news   │   │ ollama  │   │    tts    │   │ quizgen, coursegen,     │
   │PostgreSQL│   │actualités│  │ (IA)    │   │ (voix)    │   │ podcastgen, glossarygen,│
   └─────────┘    └─────────┘   └─────────┘   └───────────┘   │ narrator (files d'IA)   │
                                                              └─────────────────────────┘
```

- Tout tourne dans **Docker Compose**. Seul **nginx** publie des ports (80 et 443).
- L'application et la base n'ont **aucun accès à Internet**. Seuls les actualités (flux RSS) et les services d'IA
  (téléchargement des modèles) peuvent sortir.
- Les services d'IA (profil `ia`) sont **facultatifs** : ils demandent une carte graphique NVIDIA.
- Le script `scripts/install.sh` fait toute l'installation : secrets, certificat, démarrage, contenus, tâches
  planifiées, compte administrateur.

**Durée** : 20 à 40 minutes sans IA, 45 à 90 minutes avec IA (construction des images et téléchargement des modèles).

---

## 2. Choisir et dimensionner la machine

| | Sans IA | Avec IA locale |
|---|---|---|
| Système | Linux 64 bits : Debian 12, Ubuntu 22.04/24.04, RHEL/Rocky 9 | idem |
| Processeur | 2 vCPU | 8 vCPU |
| Mémoire | 4 Go | 32 Go |
| Disque | 60 Go SSD | 150 Go SSD |
| Carte graphique | — | NVIDIA, 8 Go de mémoire vidéo au moins (RTX 3070/4070, L4, A10…) |
| Utilisateurs simultanés (ordre de grandeur) | ~100 | ~100 en lecture ; IA : une génération à la fois et quelques questions simultanées à l'assistant |

**Espace disque.** Docker range ses images et ses volumes (base, PDF, fichiers audio) sous `/var/lib/docker`.
Prévoyez cet espace **sur la partition qui l'héberge** : un disque plein bloque Docker. Le contenu complet occupe
environ 2 Go, les images 15 à 25 Go avec l'IA, les modèles d'IA environ 10 Go, les sauvegardes quelques Go
par jour conservé.

**Sans IA**, la plateforme reste complète pour les apprenants : lecture des guides, tests, cours et podcasts déjà
produits (livrés par le pack de contenus), parcours, progression. Seules la génération de nouveaux contenus et
l'assistant de lecture sont absents. L'IA peut être activée plus tard (§ 11).

**Dans le cloud**, préférez un hébergeur européen. Pour un client public ou manipulant des données sensibles,
choisissez une offre qualifiée **SecNumCloud** (Outscale, OVHcloud, Cloud Temple, S3NS…). Ces offres proposent
des machines avec GPU NVIDIA.

---

## 3. Informations à réunir avant de commencer

| Information | Exemple | Utilisée pour |
|---|---|---|
| Nom de domaine **ou** adresse IP du serveur | `formation.client.fr` / `10.0.12.40` | adresse du site, certificat |
| Type de certificat | Let's Encrypt (domaine public), autosigné (réseau interne), ou certificat fourni par le client | HTTPS |
| Nom affiché de la plateforme | `Académie Cyber Client` | en-tête, titres, e-mails |
| Raison sociale, adresse, SIREN de l'exploitant | `Client SAS, 1 rue …, 123 456 789` | mentions légales |
| E-mail de contact et du DPO | `dpo@client.fr` | mentions légales, données personnelles |
| Hébergeur | `OVH SAS, 2 rue Kellermann, 59100 Roubaix` ou « serveurs de l'organisation » | mentions légales |
| Pseudo et e-mail de l'administrateur | `admin`, `it@client.fr` | premier compte, Let's Encrypt |
| Serveur d'envoi d'e-mails (facultatif) | `smtp.client.fr:587`, utilisateur, mot de passe, expéditeur | invitations, mot de passe oublié |
| Pack de contenus (recommandé) | `cyberlearning-contenus-2026.10.tar` (+ `.sig`) ou adresse du canal | contenus prêts à l'emploi |
| Réseau d'administration (facultatif) | `10.0.0.0/8` | réserver l'administration à ce réseau |

Sans serveur SMTP, la plateforme fonctionne : l'administrateur transmet lui-même les liens d'invitation.

---

## 4. Préparer le serveur

Les commandes ci-dessous visent Debian et Ubuntu. Sur RHEL/Rocky, utilisez `dnf` et la documentation Docker
correspondante.

### 4.1 Système à jour, utilisateur dédié

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y ca-certificates curl git openssl rsync cron unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades      # mises à jour de sécurité automatiques
sudo adduser --disabled-password --gecos "" cyber    # compte qui exploitera l'instance
```

### 4.2 Docker Engine et Compose

Suivez la documentation officielle : <https://docs.docker.com/engine/install/> (dépôt APT de Docker), puis :

```bash
sudo usermod -aG docker cyber
sudo systemctl enable --now docker
docker compose version          # doit afficher v2.24 ou plus récent
```

> Si `/var/lib/docker` est sur une petite partition, déplacez la racine de Docker vers un disque plus grand
> **avant** l'installation : `"data-root": "/data/docker"` dans `/etc/docker/daemon.json`, puis
> `sudo systemctl restart docker`.

### 4.3 Carte graphique NVIDIA (seulement avec l'IA)

1. Installez le pilote NVIDIA recommandé par votre distribution, puis redémarrez. `nvidia-smi` doit afficher la carte.
2. Installez le **NVIDIA Container Toolkit** :
   <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>
3. Déclarez-le à Docker et vérifiez :

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### 4.4 Réseau, pare-feu, DNS

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

- **Cloud** : ouvrez les mêmes ports (22, 80, 443) dans le groupe de sécurité de la machine ; limitez le port 22
  aux adresses de l'équipe d'exploitation.
- **Let's Encrypt** : l'enregistrement DNS (`formation.client.fr` → adresse publique) doit exister **avant**
  l'installation, et le port 80 doit être joignable depuis Internet.
- **Sur site** : l'installation a besoin d'un accès sortant HTTPS (images Docker, dépendances Python, pack de
  contenus par canal, modèles d'IA). Si le serveur passe par un proxy, configurez-le pour Docker
  (<https://docs.docker.com/engine/daemon/proxy/>). Pour une installation sans aucun accès Internet,
  voir § 12.6.

> Docker publie ses ports en contournant les règles `ufw`. Ici, seuls les ports 80 et 443 de nginx sont
> publiés, volontairement.

### 4.5 Durcissement SSH (recommandé)

Dans `/etc/ssh/sshd_config` : `PasswordAuthentication no`, `PermitRootLogin no`, puis `sudo systemctl reload ssh`.
Vérifiez avant de fermer votre session que la connexion par clé fonctionne.

---

## 5. Récupérer l'application

Travaillez avec le compte dédié : `sudo -iu cyber`.

**A. Archive livrée par l'éditeur** (cas d'un client) :

```bash
mkdir ~/cyberlearning && cd ~/cyberlearning
tar -xzf /chemin/cyberlearning-<version>.tar.gz
```

**B. Dépôt Git** (instance de l'éditeur, ou client ayant accès au dépôt) : créez une clé SSH en lecture seule
(« deploy key » GitHub, case *Allow write access* décochée), puis :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/cyberlearning_deploy -N "" -C "deploy@cyberlearning"
cat ~/.ssh/cyberlearning_deploy.pub              # à ajouter dans GitHub → Settings → Deploy keys
cat >> ~/.ssh/config <<'EOF'
Host github-cyberlearning
  HostName github.com
  User git
  IdentityFile ~/.ssh/cyberlearning_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
git clone git@github-cyberlearning:<compte>/CyberLearning.git ~/cyberlearning
cd ~/cyberlearning
```

Si l'éditeur vous a remis un pack de contenus, copiez-le sur le serveur, par exemple dans `~/packs/`, avec son
fichier `.sig`.

---

## 6. Lancer l'installation

### 6.1 Mode interactif

```bash
cd ~/cyberlearning
./scripts/install.sh
```

Le script pose ses questions (voir § 3), affiche ses choix et s'arrête à la première erreur.

### 6.2 Mode automatique (cloud-init, Ansible, Terraform…)

```bash
./scripts/install.sh --non-interactif \
  --domaine formation.client.fr --tls letsencrypt \
  --nom "Académie Cyber Client" --organisation "Client SAS" \
  --contact dpo@client.fr --hebergeur "OVH SAS, 2 rue Kellermann, 59100 Roubaix" \
  --admin admin --email-admin it@client.fr \
  --ia auto --contenus ~/packs/cyberlearning-contenus-2026.10.tar --taches oui \
  --smtp-hote smtp.client.fr --smtp-port 587 --smtp-utilisateur formation \
  --smtp-mot-de-passe '…' --smtp-expediteur 'Formation <formation@client.fr>'
```

| Option | Valeurs | Par défaut |
|---|---|---|
| `--domaine` | nom de domaine ou adresse IP | adresse IP du serveur |
| `--tls` | `letsencrypt`, `autosigne`, `fourni` (déposer `fullchain.pem` et `privkey.pem` dans `nginx/certs/` avant) | `letsencrypt` pour un nom de domaine, `autosigne` pour une IP |
| `--nom`, `--organisation`, `--contact`, `--hebergeur` | textes des pages légales | `CyberLearning`, vides |
| `--admin`, `--email-admin` | compte administrateur | `admin`, e-mail de contact |
| `--ia` | `auto` (selon la carte graphique), `oui`, `non` | `auto` |
| `--contenus` | pack `.tar` **ou** canal `https://…/` | aucun |
| `--taches` | `oui`/`non` : sauvegarde et purge planifiées | `oui` |
| `--images-fournies` | images déjà chargées par `docker load` : ni construction ni téléchargement (§ 12.6) | — |
| `--smtp-*` | serveur d'envoi d'e-mails | aucun |

### 6.3 Ce que fait le script

1. Vérifie les prérequis (Docker, Compose, openssl) et refuse de s'exécuter si une instance existe déjà.
2. Détecte la carte NVIDIA et active ou non l'IA.
3. Crée, avec des secrets aléatoires et les droits 600 :
   - `.env` : réglages de l'instance ;
   - `.env.db` : mots de passe de la base, lus par le seul service `db` ;
   - `.backup-key` : clé de chiffrement des sauvegardes.
4. Crée un certificat autosigné provisoire, construit les images et démarre les services.
5. Avec `--tls letsencrypt` : obtient le certificat, active HSTS, redémarre nginx.
6. Vérifie la configuration de sécurité (`manage.py check --deploy`).
7. Installe les contenus : pack de l'éditeur, ou import depuis l'ANSSI (guides, référentiels, certifications, QCM).
8. Avec l'IA : télécharge le modèle de langue (environ 6 Go).
9. Planifie dans la crontab du compte : sauvegarde chiffrée (3 h), purge des données personnelles expirées (4 h),
   et vérification hebdomadaire des contenus si un canal est configuré.
10. Crée le compte administrateur et affiche **un lien pour choisir son mot de passe** (valable 3 jours, un seul usage).

---

## 7. Vérifier l'installation

```bash
docker compose ps                                     # tous les services « Up », web « healthy »
curl -sI https://formation.client.fr | head -n 1      # HTTP/2 200 (ajoutez -k avec un certificat autosigné)
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py importer_contenus --version-installee   # version du pack installé
```

Liste de contrôle :

- [ ] Le site s'ouvre en HTTPS ; `http://` redirige vers `https://`.
- [ ] Le lien affiché par le script permet de choisir le mot de passe de l'administrateur.
- [ ] À la première connexion, l'activation de la double authentification est demandée (QR code).
- [ ] Le catalogue affiche les guides ; un guide s'ouvre ; une version audio se lit (si le pack en contient).
- [ ] Les pages **Mentions légales**, **Confidentialité**, **Conditions d'utilisation** sont remplies.
- [ ] `crontab -l` affiche les tâches `cyberlearning-tache`.
- [ ] Avec l'IA : `docker compose exec ollama ollama list` affiche le modèle ; le tableau de bord
      **Gestion → Générations** indique les services actifs.

---

## 8. Premiers pas après l'installation

1. **Clé des sauvegardes** : copiez `.backup-key` dans un coffre-fort, **hors du serveur**. Sans elle, les
   sauvegardes sont illisibles.
2. **Pages légales** : complétez `ORGANISATION_ADDRESS`, `ORGANISATION_ID` (SIREN) et `DPO_EMAIL` dans `.env`,
   puis `docker compose up -d web`. Faites relire les pages légales par un juriste.
3. **Double authentification** de l'administrateur : activez-la à la première connexion et gardez les codes de
   secours.
4. **Comptes** : **Gestion → Utilisateurs et rôles → Créer un compte**. Un lien d'invitation est envoyé par e-mail
   (ou affiché, à transmettre sans e-mail). Rôles : apprenant, rédacteur, gestionnaire de contenu. Les
   inscriptions publiques sont fermées (`SIGNUP_ENABLED=0`).
5. **Copie distante des sauvegardes** : `BACKUP_REMOTE=utilisateur@serveur:/chemin` dans `.env` (rsync par clé SSH).
6. **Administration réservée à un réseau** (recommandé) : `ADMIN_ALLOW_FROM=10.0.0.0/8` dans `.env`, puis
   `docker compose up -d nginx`.
7. **Restauration** : faites un premier test de restauration sur une machine de test (§ 10.3).

---

## 9. Contenus : packs et mises à jour

Les contenus (guides et PDF, index de recherche, référentiels, certifications, QCM, lexiques, versions audio,
parcours, cours, podcasts) sont livrés dans un **pack de contenus** produit par l'éditeur. Un pack ne contient
**aucune donnée personnelle** : ni comptes, ni progression, ni lectures, ni résultats.

```bash
./scripts/mettre-a-jour-contenus.sh ~/packs/cyberlearning-contenus-2026.11.tar   # pack reçu
./scripts/mettre-a-jour-contenus.sh https://maj.editeur.fr/contenus/            # canal de l'éditeur
./scripts/mettre-a-jour-contenus.sh                                             # canal de .env (CONTENT_UPDATE_URL)
./scripts/mettre-a-jour-contenus.sh pack.tar --essai                            # bilan sans rien changer
```

Une mise à jour :

- fait une sauvegarde chiffrée, puis vérifie la **signature de l'éditeur** (si `contenus.pub` est présent),
  l'empreinte de chaque fichier et la compatibilité avec la version de l'application ;
- ajoute les nouveaux contenus et met à jour les autres **sur place** : la progression, les tests passés et les
  écoutes restent valables ;
- **n'écrase jamais** un contenu modifié, masqué ou repris en rédaction sur l'instance, ni un contenu écrit par ses
  rédacteurs (ils sont listés dans le bilan) ; `--forcer` remplace aussi les contenus modifiés ;
- ne supprime rien. L'historique est dans **Administration → Imports de contenus**.

Avec un canal (`CONTENT_UPDATE_URL` dans `.env`), la vérification se fait chaque dimanche à 4 h 30 ; le pack n'est
téléchargé que si une nouvelle version est publiée.

**Côté éditeur** (instance de référence, où les contenus sont générés et relus) :

```bash
./scripts/publier-contenus.sh --creer-cle    # une fois : contenus-signature.key (secrète), contenus.pub (à livrer)
./scripts/publier-contenus.sh 2026.11        # packs/cyberlearning-contenus-2026.11.tar + .sig + version.txt
```

Remettez le `.tar` et son `.sig` au client, ou publiez le dossier `packs/` sur un serveur HTTPS : c'est le canal.

---

## 10. Exploiter l'instance

### 10.1 Commandes courantes

```bash
docker compose ps                                  # état des services
docker compose logs -f web                         # journaux de l'application (et journal d'audit)
docker compose logs nginx | grep " 429 "           # requêtes bloquées par la limitation de débit
docker compose exec web python manage.py axes_reset             # débloquer les comptes verrouillés
docker compose exec web python manage.py creer_admin <pseudo> --url https://formation.client.fr   # lien admin
docker compose exec web python manage.py purge_donnees          # purge des données expirées (déjà planifiée)
```

### 10.2 Mettre à jour l'application et les contenus

```bash
./scripts/deploy.sh ~/packs/cyberlearning-1.1.0.tar.gz     # archive livrée par l'éditeur (signature vérifiée)
./scripts/deploy.sh                                        # ou : dépôt Git
./scripts/mettre-a-jour-contenus.sh                        # contenus (voir § 9)
```

Chaque mise à jour commence par une sauvegarde chiffrée ; les comptes, la progression et les réglages ne sont jamais
touchés. Procédure détaillée, vérifications et retour en arrière :
[Mettre à jour une instance](mettre-a-jour-une-instance.md).

### 10.3 Sauvegardes et restauration

- Sauvegarde chiffrée (AES-256) chaque nuit à 3 h dans `backups/`, 14 jours conservés (`BACKUP_KEEP_DAYS`),
  copiée vers `BACKUP_REMOTE` si renseigné. Manuellement : `./scripts/backup.sh`.
- Restauration (remplace la base actuelle, demande confirmation) :

```bash
./scripts/restore.sh backups/db-AAAAMMJJ-HHMMSS.dump.enc backups/media-AAAAMMJJ-HHMMSS.tar.gz.enc
```

Testez une restauration chaque mois sur une machine de test : une sauvegarde jamais restaurée n'est pas une sauvegarde.

### 10.4 Surveillance

- Disque : `df -h /var/lib/docker` et `docker system df` (prévoir une alerte à 80 %).
- Certificat Let's Encrypt : renouvelé automatiquement (service `certbot`) et rechargé par nginx toutes les 6 h.
- Sécurité : **Administration → Journal d'audit** (connexions, échecs, rôles, publications, suppressions).
- Disponibilité : supervisez `https://formation.client.fr/healthz/` (réponse `ok`).

---

## 11. Activer l'IA plus tard

1. Installez le pilote NVIDIA et le NVIDIA Container Toolkit (§ 4.3).
2. Dans `.env` : `AI_ENABLED=1`, et `COMPOSE_PROFILES=ia` (`ia,letsencrypt` si Let's Encrypt est utilisé).
3. Puis :

```bash
docker compose build && docker compose up -d
docker compose exec ollama ollama pull gemma3:12b
docker compose exec ollama ollama pull qwen3:14b
```

---

## 12. Dépannage

### 12.1 Let's Encrypt a échoué

Vérifiez le DNS (`dig +short formation.client.fr`) et que le port 80 est joignable depuis Internet, puis :

```bash
docker compose run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/acme \
  -d formation.client.fr --email it@client.fr --agree-tos --non-interactive --no-eff-email \
  --deploy-hook "chgrp -R 101 /etc/letsencrypt/live /etc/letsencrypt/archive && chmod -R g+rX /etc/letsencrypt/live /etc/letsencrypt/archive"
```

Dans `.env` : `SSL_CERTIFICATE=/etc/letsencrypt/live/formation.client.fr/fullchain.pem`,
`SSL_CERTIFICATE_KEY=/etc/letsencrypt/live/formation.client.fr/privkey.pem`, `HSTS_VALUE=max-age=31536000`,
`DJANGO_HSTS_SECONDS=31536000`, et `letsencrypt` dans `COMPOSE_PROFILES`. Puis
`docker compose up -d --force-recreate web nginx`.

### 12.2 Le service web ne démarre pas

`docker compose logs web` : la cause est en fin de journal (base injoignable, variable manquante dans `.env`,
migration en échec).

### 12.3 « Read-only file system », Docker ne répond plus

Le disque est plein. Libérez de l'espace (`docker system prune` supprime les images et caches inutilisés, **pas**
les volumes), ou déplacez la racine de Docker vers un disque plus grand (§ 4.2).

### 12.4 Un administrateur a perdu son téléphone (double authentification)

Un autre administrateur supprime la ligne du compte dans **Administration → Doubles authentifications** ; le compte
réactivera la double authentification à sa prochaine connexion. S'il n'y a qu'un administrateur :
`docker compose exec web python manage.py shell -c "from accounts.models import TwoFactor; TwoFactor.objects.filter(user__username='admin').delete()"`.

### 12.5 Un compte est verrouillé après des échecs de connexion

`docker compose exec web python manage.py axes_reset` (ou attendre 30 minutes).

### 12.6 Installation sans accès Internet

Construisez les images et préparez le pack sur une machine connectée, puis transférez-les :

```bash
docker compose build && docker save cyberlearning-web:latest | gzip > web.tar.gz   # machine connectée
docker load < web.tar.gz                                                          # serveur cible
```

Faites de même pour `postgres:17-alpine` et `nginxinc/nginx-unprivileged:1.27-alpine` (avec l'IA : aussi
`cyberlearning-tts:latest` et `ollama/ollama:0.34.2`), puis lancez sur le serveur :

```bash
./scripts/install.sh --images-fournies --contenus ~/packs/cyberlearning-contenus-2026.10.tar
```

Aucun téléchargement n'est alors nécessaire, hormis les modèles d'IA : copiez les volumes `ollama_models` et
`tts_models` depuis la machine connectée (`docker run --rm -v <volume>:/v alpine tar -C /v -czf - .`).

---

## 13. Désinstaller

```bash
docker compose down                     # arrête les services ; les données (volumes) sont conservées
docker compose down -v                  # ⚠ supprime aussi la base, les fichiers et les certificats
crontab -l | grep -v cyberlearning-tache | crontab -   # retire les tâches planifiées
```

Gardez une dernière sauvegarde (et `.backup-key`) avant toute suppression définitive.

---

## 14. Annexes

### 14.1 Fichiers créés par l'installation (jamais dans le dépôt)

| Fichier | Contenu | Droits |
|---|---|---|
| `.env` | réglages de l'instance, secrets de l'application | 600 |
| `.env.db` | mot de passe du super-utilisateur PostgreSQL et du rôle applicatif | 600 |
| `.backup-key` | clé de chiffrement des sauvegardes (copie hors serveur obligatoire) | 600 |
| `nginx/certs/` | certificat autosigné ou fourni | 644 / 640 |
| `backups/` | sauvegardes chiffrées et journaux | 700 |

### 14.2 Principaux réglages de `.env`

| Variable | Rôle |
|---|---|
| `INSTANCE_NAME`, `ORGANISATION_*`, `CONTACT_EMAIL`, `DPO_EMAIL`, `HOSTING_PROVIDER` | nom affiché et pages légales |
| `AI_ENABLED`, `COMPOSE_PROFILES` | IA locale, services démarrés |
| `PREREVIEW_ENABLED` | pré-relecteur IA des QCM et cours générés (1 par défaut ; 0 pour le couper) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | envoi d'e-mails |
| `SIGNUP_ENABLED` | inscriptions publiques (0 par défaut) |
| `MFA_REQUIRED_FOR_PRIVILEGED`, `PRIVILEGED_IDLE_MINUTES` | double authentification, déconnexion après inactivité |
| `SERVER_NAME`, `SSL_CERTIFICATE`, `SSL_CERTIFICATE_KEY`, `HSTS_VALUE`, `ADMIN_ALLOW_FROM` | nginx |
| `THIRD_PARTY_EXAM_OUTLINES` | programmes d'examen des certifications tierces (0 pour les masquer) |
| `CONTENT_UPDATE_URL` | canal de mise à jour des contenus |
| `BACKUP_KEEP_DAYS`, `BACKUP_REMOTE` | sauvegardes |
| `AUDIT_RETENTION_DAYS` | conservation du journal d'audit (365 jours) |

Après une modification de `.env` : `docker compose up -d` (les services concernés sont recréés).

### 14.3 Mesures de sécurité en place

| Domaine | Mesure |
|---|---|
| Réseau | seul nginx publie des ports ; application, base et IA sur des réseaux internes sans accès Internet ; nom d'hôte inconnu refusé |
| Chiffrement | TLS 1.2/1.3 avec suites recommandées par l'ANSSI, HSTS avec un certificat définitif |
| Base de données | l'application utilise un rôle sans droits d'administration ; le mot de passe du super-utilisateur n'est lu que par `db` |
| Conteneurs | non-root, système de fichiers en lecture seule, capacités retirées, `no-new-privileges`, limites de mémoire et de processus |
| Comptes | double authentification obligatoire pour les rôles élevés, déconnexion après 30 min d'inactivité, verrouillage après 5 échecs, invitations à usage unique |
| Traçabilité | journal d'audit conservé 365 jours |
| Données personnelles | export et suppression par chaque utilisateur, purge automatique selon les durées de conservation, aucune donnée personnelle dans les packs de contenus |
| Sauvegardes | chiffrées (AES-256), quotidiennes, copie distante possible, sauvegarde automatique avant chaque mise à jour |
