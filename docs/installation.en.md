# CyberLearning installation procedure

*Version française : [installation.md](installation.md)*

This document describes the complete installation of a CyberLearning instance on new infrastructure, then how to
operate it (updates, backups, troubleshooting). It is intended for a system administrator comfortable with Linux
and Docker.

**Principle**: one instance = one customer. The same package installs **on premises** (the customer's server) or
**in the cloud** (a virtual machine dedicated to the customer), with the same procedure. Each customer's data is
isolated by design.

> The application's interface and messages are in French; the commands and option names below are used as is.

---

## Contents

1. [Overview](#1-overview)
2. [Choosing and sizing the machine](#2-choosing-and-sizing-the-machine)
3. [Information to gather before starting](#3-information-to-gather-before-starting)
4. [Preparing the server](#4-preparing-the-server)
5. [Getting the application](#5-getting-the-application)
6. [Running the installation](#6-running-the-installation)
7. [Checking the installation](#7-checking-the-installation)
8. [First steps after installation](#8-first-steps-after-installation)
9. [Content: packs and updates](#9-content-packs-and-updates)
10. [Operating the instance](#10-operating-the-instance)
11. [Enabling AI later](#11-enabling-ai-later)
12. [Troubleshooting](#12-troubleshooting)
13. [Uninstalling](#13-uninstalling)
14. [Appendices: settings, files, security](#14-appendices)

---

## 1. Overview

```
                          Internet / corporate network
                                     │  ports 80 and 443
                              ┌──────▼──────┐
                              │    nginx    │  TLS, security headers, rate limiting
                              └──────┬──────┘
                    internal "proxy" network (no Internet access)
                              ┌──────▼──────┐
                              │     web     │  Django application (gunicorn)
                              └──────┬──────┘
                    internal "backend" network (no Internet access)
        ┌──────────────┬─────────────┼──────────────┬──────────────────────┐
   ┌────▼────┐    ┌────▼────┐   ┌────▼────┐   ┌─────▼─────┐   ┌────────────▼───────────┐
   │   db    │    │  news   │   │ ollama  │   │    tts    │   │ quizgen, coursegen,     │
   │PostgreSQL│   │ RSS news │  │  (AI)   │   │ (voice)   │   │ podcastgen, glossarygen,│
   └─────────┘    └─────────┘   └─────────┘   └───────────┘   │ narrator (AI queues)    │
                                                              └─────────────────────────┘
```

- Everything runs in **Docker Compose**. Only **nginx** publishes ports (80 and 443).
- The application and the database have **no Internet access**. Only the news service (RSS feeds) and the AI
  services (model downloads) can reach out.
- The AI services (`ia` profile) are **optional**: they require an NVIDIA graphics card.
- The `scripts/install.sh` script performs the whole installation: secrets, certificate, startup, content,
  scheduled tasks, administrator account.

**Duration**: 20 to 40 minutes without AI, 45 to 90 minutes with AI (image builds and model downloads).

---

## 2. Choosing and sizing the machine

| | Without AI | With local AI |
|---|---|---|
| Operating system | 64-bit Linux: Debian 12, Ubuntu 22.04/24.04, RHEL/Rocky 9 | same |
| CPU | 2 vCPU | 8 vCPU |
| Memory | 4 GB | 32 GB |
| Disk | 60 GB SSD | 150 GB SSD |
| Graphics card | — | NVIDIA, at least 8 GB of video memory (RTX 3070/4070, L4, A10…) |
| Concurrent users (order of magnitude) | ~100 | ~100 reading; AI: one generation at a time and a few simultaneous assistant questions |

**Disk space.** Docker stores its images and volumes (database, PDFs, audio files) under `/var/lib/docker`.
Plan this space **on the partition that hosts it**: a full disk blocks Docker. The full content takes about 2 GB,
the images 15 to 25 GB with AI, the AI models about 10 GB, and backups a few GB per retained day.

**Without AI**, the platform remains complete for learners: reading guides, tests, courses and podcasts already
produced (delivered in the content pack), learning paths, progress. Only the generation of new content and the
reading assistant are missing. AI can be enabled later (§ 11).

**In the cloud**, prefer a European provider. For a public-sector customer or one handling sensitive data, choose
a **SecNumCloud**-qualified offering (Outscale, OVHcloud, Cloud Temple, S3NS…). These offerings provide machines
with NVIDIA GPUs.

---

## 3. Information to gather before starting

| Information | Example | Used for |
|---|---|---|
| Server domain name **or** IP address | `training.customer.com` / `10.0.12.40` | site address, certificate |
| Certificate type | Let's Encrypt (public domain), self-signed (internal network), or a certificate supplied by the customer | HTTPS |
| Platform display name | `Customer Cyber Academy` | header, titles, emails |
| Operator's company name, address, registration number | `Customer SAS, 1 rue …, 123 456 789` | legal notice |
| Contact and DPO email addresses | `dpo@customer.com` | legal notice, personal data |
| Hosting provider | `OVH SAS, 2 rue Kellermann, 59100 Roubaix` or "the organisation's servers" | legal notice |
| Administrator username and email | `admin`, `it@customer.com` | first account, Let's Encrypt |
| Outgoing mail server (optional) | `smtp.customer.com:587`, user, password, sender | invitations, forgotten password |
| Content pack (recommended) | `cyberlearning-contenus-2026.10.tar` (+ `.sig`) or update channel address | ready-to-use content |
| Administration network (optional) | `10.0.0.0/8` | restrict administration to this network |

Without an SMTP server, the platform still works: the administrator hands out invitation links themselves.

---

## 4. Preparing the server

The commands below target Debian and Ubuntu. On RHEL/Rocky, use `dnf` and the corresponding Docker documentation.

### 4.1 Up-to-date system, dedicated user

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y ca-certificates curl git openssl rsync cron unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades      # automatic security updates
sudo adduser --disabled-password --gecos "" cyber    # account that will operate the instance
```

### 4.2 Docker Engine and Compose

Follow the official documentation: <https://docs.docker.com/engine/install/> (Docker APT repository), then:

```bash
sudo usermod -aG docker cyber
sudo systemctl enable --now docker
docker compose version          # must show v2.24 or later
```

> If `/var/lib/docker` sits on a small partition, move Docker's root to a larger disk **before** installing:
> `"data-root": "/data/docker"` in `/etc/docker/daemon.json`, then `sudo systemctl restart docker`.

### 4.3 NVIDIA graphics card (only with AI)

1. Install the NVIDIA driver recommended by your distribution, then reboot. `nvidia-smi` must show the card.
2. Install the **NVIDIA Container Toolkit**:
   <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>
3. Register it with Docker and check:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### 4.4 Network, firewall, DNS

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

- **Cloud**: open the same ports (22, 80, 443) in the machine's security group; restrict port 22 to the operations
  team's addresses.
- **Let's Encrypt**: the DNS record (`training.customer.com` → public address) must exist **before** installation,
  and port 80 must be reachable from the Internet.
- **On premises**: installation needs outbound HTTPS access (Docker images, Python dependencies, content pack via
  channel, AI models). If the server goes through a proxy, configure it for Docker
  (<https://docs.docker.com/engine/daemon/proxy/>). For an installation without any Internet access, see § 12.6.

> Docker publishes its ports by bypassing `ufw` rules. Here, only nginx's ports 80 and 443 are published, on purpose.

### 4.5 SSH hardening (recommended)

In `/etc/ssh/sshd_config`: `PasswordAuthentication no`, `PermitRootLogin no`, then `sudo systemctl reload ssh`.
Before closing your session, check that key-based login works.

---

## 5. Getting the application

Work with the dedicated account: `sudo -iu cyber`.

**A. Archive delivered by the vendor** (customer case):

```bash
mkdir ~/cyberlearning && cd ~/cyberlearning
tar -xzf /path/cyberlearning-<version>.tar.gz
```

**B. Git repository** (vendor's instance, or a customer with repository access): create a read-only SSH key
(GitHub "deploy key", *Allow write access* unchecked), then:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/cyberlearning_deploy -N "" -C "deploy@cyberlearning"
cat ~/.ssh/cyberlearning_deploy.pub              # add in GitHub → Settings → Deploy keys
cat >> ~/.ssh/config <<'EOF'
Host github-cyberlearning
  HostName github.com
  User git
  IdentityFile ~/.ssh/cyberlearning_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
git clone git@github-cyberlearning:<account>/CyberLearning.git ~/cyberlearning
cd ~/cyberlearning
```

If the vendor gave you a content pack, copy it to the server, for example into `~/packs/`, together with its
`.sig` file.

---

## 6. Running the installation

### 6.1 Interactive mode

```bash
cd ~/cyberlearning
./scripts/install.sh
```

The script asks its questions (in French; see § 3 for what they cover), shows its choices and stops at the first error.

### 6.2 Unattended mode (cloud-init, Ansible, Terraform…)

```bash
./scripts/install.sh --non-interactif \
  --domaine training.customer.com --tls letsencrypt \
  --nom "Customer Cyber Academy" --organisation "Customer SAS" \
  --contact dpo@customer.com --hebergeur "OVH SAS, 2 rue Kellermann, 59100 Roubaix" \
  --admin admin --email-admin it@customer.com \
  --ia auto --contenus ~/packs/cyberlearning-contenus-2026.10.tar --taches oui \
  --smtp-hote smtp.customer.com --smtp-port 587 --smtp-utilisateur training \
  --smtp-mot-de-passe '…' --smtp-expediteur 'Training <training@customer.com>'
```

| Option | Values | Default |
|---|---|---|
| `--domaine` | domain name or IP address | server IP address |
| `--tls` | `letsencrypt`, `autosigne` (self-signed), `fourni` (supplied: put `fullchain.pem` and `privkey.pem` in `nginx/certs/` first) | `letsencrypt` for a domain name, `autosigne` for an IP |
| `--nom`, `--organisation`, `--contact`, `--hebergeur` | display name and legal page texts | `CyberLearning`, empty |
| `--admin`, `--email-admin` | administrator account | `admin`, contact email |
| `--ia` | `auto` (based on the graphics card), `oui` (yes), `non` (no) | `auto` |
| `--contenus` | `.tar` pack **or** `https://…/` channel | none |
| `--taches` | `oui`/`non`: scheduled backup and purge | `oui` |
| `--images-fournies` | images already loaded with `docker load`: no build, no download (§ 12.6) | — |
| `--smtp-*` | outgoing mail server | none |

### 6.3 What the script does

1. Checks the prerequisites (Docker, Compose, openssl) and refuses to run if an instance already exists.
2. Detects the NVIDIA card and enables AI or not.
3. Creates, with random secrets and mode 600:
   - `.env`: instance settings;
   - `.env.db`: database passwords, read only by the `db` service;
   - `.backup-key`: backup encryption key.
4. Creates a temporary self-signed certificate, builds the images and starts the services.
5. With `--tls letsencrypt`: obtains the certificate, enables HSTS, restarts nginx.
6. Checks the security configuration (`manage.py check --deploy`).
7. Installs the content: the vendor's pack, or an import from the ANSSI (guides, frameworks, certifications, quizzes).
8. With AI: downloads the language model (about 6 GB).
9. Schedules in the account's crontab: encrypted backup (3 am), purge of expired personal data (4 am), and a
   weekly content check if a channel is configured.
10. Creates the administrator account and displays **a link to choose its password** (valid 3 days, single use).

---

## 7. Checking the installation

```bash
docker compose ps                                       # all services "Up", web "healthy"
curl -sI https://training.customer.com | head -n 1      # HTTP/2 200 (add -k with a self-signed certificate)
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py importer_contenus --version-installee   # installed pack version
```

Checklist:

- [ ] The site opens over HTTPS; `http://` redirects to `https://`.
- [ ] The link displayed by the script lets you choose the administrator's password.
- [ ] At first login, two-factor authentication setup is requested (QR code).
- [ ] The catalogue shows the guides; a guide opens; an audio version plays (if the pack contains any).
- [ ] The **Mentions légales**, **Confidentialité** and **Conditions d'utilisation** pages (legal notice, privacy,
      terms of use) are filled in.
- [ ] `crontab -l` shows the `cyberlearning-tache` jobs.
- [ ] With AI: `docker compose exec ollama ollama list` shows the model; the **Gestion → Générations** dashboard
      shows the services as active.

---

## 8. First steps after installation

1. **Backup key**: copy `.backup-key` into a password vault, **off the server**. Without it, backups are unreadable.
2. **Legal pages**: fill in `ORGANISATION_ADDRESS`, `ORGANISATION_ID` (registration number) and `DPO_EMAIL` in
   `.env`, then `docker compose up -d web`. Have a lawyer review the legal pages.
3. **Two-factor authentication** for the administrator: enable it at first login and keep the recovery codes.
4. **Accounts**: **Gestion → Utilisateurs et rôles → Créer un compte**. An invitation link is emailed (or displayed,
   to hand out, without email). Roles: learner, writer (*rédacteur*), content manager (*gestionnaire de contenu*).
   Public sign-up is closed (`SIGNUP_ENABLED=0`).
5. **Off-site backup copy**: `BACKUP_REMOTE=user@server:/path` in `.env` (rsync over SSH key).
6. **Administration restricted to a network** (recommended): `ADMIN_ALLOW_FROM=10.0.0.0/8` in `.env`, then
   `docker compose up -d nginx`.
7. **Restore**: run a first restore test on a test machine (§ 10.3).

---

## 9. Content: packs and updates

The content (guides and PDFs, search index, frameworks, certifications, quizzes, glossaries, audio versions,
learning paths, courses, podcasts) is delivered in a **content pack** produced by the vendor. A pack contains
**no personal data**: no accounts, progress, reading history or results.

```bash
./scripts/mettre-a-jour-contenus.sh ~/packs/cyberlearning-contenus-2026.11.tar   # received pack
./scripts/mettre-a-jour-contenus.sh https://updates.vendor.com/contenus/        # vendor channel
./scripts/mettre-a-jour-contenus.sh                                             # channel from .env (CONTENT_UPDATE_URL)
./scripts/mettre-a-jour-contenus.sh pack.tar --essai                            # dry run: report without changes
```

An update:

- takes an encrypted backup, then checks the **vendor's signature** (if `contenus.pub` is present), the fingerprint
  of every file and compatibility with the application version;
- adds new content and updates the rest **in place**: progress, completed tests and listening history stay valid;
- **never overwrites** content modified, hidden or taken back into drafting on the instance, nor content written by
  its own writers (they are listed in the report); `--forcer` also replaces modified content;
- deletes nothing. The history is in **Administration → Imports de contenus**.

With a channel (`CONTENT_UPDATE_URL` in `.env`), the check runs every Sunday at 4:30 am; the pack is downloaded
only when a new version is published.

**Vendor side** (reference instance, where content is generated and reviewed):

```bash
./scripts/publier-contenus.sh --creer-cle    # once: contenus-signature.key (secret), contenus.pub (to ship)
./scripts/publier-contenus.sh 2026.11        # packs/cyberlearning-contenus-2026.11.tar + .sig + version.txt
```

Hand the `.tar` and its `.sig` to the customer, or publish the `packs/` folder on an HTTPS server: that is the channel.

---

## 10. Operating the instance

### 10.1 Everyday commands

```bash
docker compose ps                                  # service status
docker compose logs -f web                         # application logs (including the audit log)
docker compose logs nginx | grep " 429 "           # requests blocked by rate limiting
docker compose exec web python manage.py axes_reset             # unlock locked accounts
docker compose exec web python manage.py creer_admin <username> --url https://training.customer.com   # admin link
docker compose exec web python manage.py purge_donnees          # purge expired data (already scheduled)
```

### 10.2 Updating the application and the content

```bash
./scripts/deploy.sh ~/packs/cyberlearning-1.1.0.tar.gz     # archive delivered by the vendor (signature checked)
./scripts/deploy.sh                                        # or: Git repository
./scripts/mettre-a-jour-contenus.sh                        # content (see § 9)
```

Every update starts with an encrypted backup; accounts, progress and settings are never touched. Detailed
procedure, checks and rollback (in French): [Mettre à jour une instance](mettre-a-jour-une-instance.md).

### 10.3 Backups and restore

- Encrypted backup (AES-256) every night at 3 am in `backups/`, 14 days retained (`BACKUP_KEEP_DAYS`), copied to
  `BACKUP_REMOTE` if set. Manually: `./scripts/backup.sh`.
- Restore (replaces the current database, asks for confirmation):

```bash
./scripts/restore.sh backups/db-YYYYMMDD-HHMMSS.dump.enc backups/media-YYYYMMDD-HHMMSS.tar.gz.enc
```

Test a restore every month on a test machine: a backup that has never been restored is not a backup.

### 10.4 Monitoring

- Disk: `df -h /var/lib/docker` and `docker system df` (set an alert at 80 %).
- Let's Encrypt certificate: renewed automatically (`certbot` service) and reloaded by nginx every 6 hours.
- Security: **Administration → Journal d'audit** (logins, failures, roles, publications, deletions).
- Availability: monitor `https://training.customer.com/healthz/` (response `ok`).

---

## 11. Enabling AI later

1. Install the NVIDIA driver and the NVIDIA Container Toolkit (§ 4.3).
2. In `.env`: `AI_ENABLED=1`, and `COMPOSE_PROFILES=ia` (`ia,letsencrypt` if Let's Encrypt is used).
3. Then:

```bash
docker compose build && docker compose up -d
docker compose exec ollama ollama pull gemma3:12b
docker compose exec ollama ollama pull qwen3:14b
```

---

## 12. Troubleshooting

### 12.1 Let's Encrypt failed

Check DNS (`dig +short training.customer.com`) and that port 80 is reachable from the Internet, then:

```bash
docker compose run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/acme \
  -d training.customer.com --email it@customer.com --agree-tos --non-interactive --no-eff-email \
  --deploy-hook "chgrp -R 101 /etc/letsencrypt/live /etc/letsencrypt/archive && chmod -R g+rX /etc/letsencrypt/live /etc/letsencrypt/archive"
```

In `.env`: `SSL_CERTIFICATE=/etc/letsencrypt/live/training.customer.com/fullchain.pem`,
`SSL_CERTIFICATE_KEY=/etc/letsencrypt/live/training.customer.com/privkey.pem`, `HSTS_VALUE=max-age=31536000`,
`DJANGO_HSTS_SECONDS=31536000`, and `letsencrypt` in `COMPOSE_PROFILES`. Then
`docker compose up -d --force-recreate web nginx`.

### 12.2 The web service does not start

`docker compose logs web`: the cause is at the end of the log (database unreachable, variable missing from `.env`,
failed migration).

### 12.3 "Read-only file system", Docker stops responding

The disk is full. Free some space (`docker system prune` removes unused images and caches, **not** volumes), or
move Docker's root to a larger disk (§ 4.2).

### 12.4 An administrator lost their phone (two-factor authentication)

Another administrator deletes the account's row in **Administration → Doubles authentifications**; the account will
set up two-factor authentication again at its next login. If there is only one administrator:
`docker compose exec web python manage.py shell -c "from accounts.models import TwoFactor; TwoFactor.objects.filter(user__username='admin').delete()"`.

### 12.5 An account is locked after failed logins

`docker compose exec web python manage.py axes_reset` (or wait 30 minutes).

### 12.6 Installation without Internet access

Build the images and prepare the pack on a connected machine, then transfer them:

```bash
docker compose build && docker save cyberlearning-web:latest | gzip > web.tar.gz   # connected machine
docker load < web.tar.gz                                                          # target server
```

Do the same for `postgres:17-alpine` and `nginxinc/nginx-unprivileged:1.27-alpine` (with AI, also
`cyberlearning-tts:latest` and `ollama/ollama:0.34.2`), then run on the server:

```bash
./scripts/install.sh --images-fournies --contenus ~/packs/cyberlearning-contenus-2026.10.tar
```

No download is then needed, except the AI models: copy the `ollama_models` and `tts_models` volumes from the
connected machine (`docker run --rm -v <volume>:/v alpine tar -C /v -czf - .`).

---

## 13. Uninstalling

```bash
docker compose down                     # stops the services; data (volumes) is kept
docker compose down -v                  # ⚠ also deletes the database, files and certificates
crontab -l | grep -v cyberlearning-tache | crontab -   # removes the scheduled tasks
```

Keep a last backup (and `.backup-key`) before any permanent deletion.

---

## 14. Appendices

### 14.1 Files created by the installation (never in the repository)

| File | Content | Mode |
|---|---|---|
| `.env` | instance settings, application secrets | 600 |
| `.env.db` | PostgreSQL superuser and application role passwords | 600 |
| `.backup-key` | backup encryption key (an off-server copy is mandatory) | 600 |
| `nginx/certs/` | self-signed or supplied certificate | 644 / 640 |
| `backups/` | encrypted backups and logs | 700 |

### 14.2 Main `.env` settings

| Variable | Purpose |
|---|---|
| `INSTANCE_NAME`, `ORGANISATION_*`, `CONTACT_EMAIL`, `DPO_EMAIL`, `HOSTING_PROVIDER` | display name and legal pages |
| `AI_ENABLED`, `COMPOSE_PROFILES` | local AI, services started |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | outgoing email |
| `SIGNUP_ENABLED` | public sign-up (0 by default) |
| `MFA_REQUIRED_FOR_PRIVILEGED`, `PRIVILEGED_IDLE_MINUTES` | two-factor authentication, logout after inactivity |
| `SERVER_NAME`, `SSL_CERTIFICATE`, `SSL_CERTIFICATE_KEY`, `HSTS_VALUE`, `ADMIN_ALLOW_FROM` | nginx |
| `THIRD_PARTY_EXAM_OUTLINES` | third-party certification exam outlines (0 to hide them) |
| `CONTENT_UPDATE_URL` | content update channel |
| `BACKUP_KEEP_DAYS`, `BACKUP_REMOTE` | backups |
| `AUDIT_RETENTION_DAYS` | audit log retention (365 days) |

After changing `.env`: `docker compose up -d` (the affected services are recreated).

### 14.3 Security measures in place

| Area | Measure |
|---|---|
| Network | only nginx publishes ports; application, database and AI on internal networks without Internet access; unknown host names rejected |
| Encryption | TLS 1.2/1.3 with ANSSI-recommended cipher suites, HSTS with a final certificate |
| Database | the application uses a role without administrative rights; the superuser password is read only by `db` |
| Containers | non-root, read-only file system, capabilities dropped, `no-new-privileges`, memory and process limits |
| Accounts | mandatory two-factor authentication for privileged roles, logout after 30 minutes of inactivity, lockout after 5 failures, single-use invitations |
| Traceability | audit log kept for 365 days |
| Personal data | export and deletion by each user, automatic purge according to retention periods, no personal data in content packs |
| Backups | encrypted (AES-256), daily, optional off-site copy, automatic backup before each update |
