#!/usr/bin/env bash
# Installation d'une instance CyberLearning (une par client), sur site ou sur une machine virtuelle cloud.
#
#   ./scripts/install.sh                      questions posées une à une
#   ./scripts/install.sh --non-interactif --domaine formation.exemple.fr --tls letsencrypt \
#       --admin admin --email-admin admin@exemple.fr --organisation "Exemple SAS" --ia auto #       --contenus https://maj.editeur.fr/contenus/      (ou un fichier cyberlearning-contenus-….tar)
#
# Prérequis : Linux, Docker Engine 24+ avec Compose v2.24+, openssl ; pour l'IA, une carte NVIDIA (8 Go de mémoire
# vidéo au moins) avec le NVIDIA Container Toolkit. Voir docs/installation.md.
set -euo pipefail
cd "$(dirname "$0")/.."

# --- Options --------------------------------------------------------------------------------------------------
INTERACTIVE=1; DOMAIN=""; TLS=""; ADMIN=""; ADMIN_EMAIL=""; ORG=""; NAME=""; CONTACT=""; AI="auto"; IMPORT=""
SMTP_HOST=""; SMTP_PORT="587"; SMTP_USER=""; SMTP_PASSWORD=""; SMTP_FROM=""; CRON=""; HOSTING=""; CONTENT=""; BUILD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --non-interactif) INTERACTIVE=0 ;;
    --domaine) DOMAIN="$2"; shift ;;
    --tls) TLS="$2"; shift ;;                     # letsencrypt | autosigne | fourni
    --admin) ADMIN="$2"; shift ;;
    --email-admin) ADMIN_EMAIL="$2"; shift ;;
    --organisation) ORG="$2"; shift ;;
    --nom) NAME="$2"; shift ;;
    --contact) CONTACT="$2"; shift ;;
    --hebergeur) HOSTING="$2"; shift ;;
    --ia) AI="$2"; shift ;;                       # auto | oui | non
    --import) IMPORT="$2"; shift ;;               # oui | non (sans pack : import ANSSI, dépôt de l'éditeur)
    --contenus) CONTENT="$2"; shift ;;            # pack de contenus : fichier .tar ou canal https://…/
    --smtp-hote) SMTP_HOST="$2"; shift ;;
    --smtp-port) SMTP_PORT="$2"; shift ;;
    --smtp-utilisateur) SMTP_USER="$2"; shift ;;
    --smtp-mot-de-passe) SMTP_PASSWORD="$2"; shift ;;
    --smtp-expediteur) SMTP_FROM="$2"; shift ;;
    --taches) CRON="$2"; shift ;;                 # oui | non : sauvegarde et purge planifiées
    --images-fournies) BUILD=0 ;;                 # images déjà chargées (docker load) : rien à construire ni télécharger
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1 (--help)"; exit 1 ;;
  esac
  shift
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
ask() {  # ask VARIABLE "Question" "défaut"
  local var="$1" question="$2" default="${3:-}" answer
  if [ -n "${!var}" ] || [ "$INTERACTIVE" = 0 ]; then
    [ -n "${!var}" ] || printf -v "$var" '%s' "$default"; return
  fi
  read -r -p "$question${default:+ [$default]} : " answer
  printf -v "$var" '%s' "${answer:-$default}"
}
secret() { openssl rand -base64 "${1:-48}" | tr -d '/+=\n' | cut -c1-"${2:-64}"; }

# --- Vérifications --------------------------------------------------------------------------------------------
[ -f .env ] && die "Une instance est déjà configurée ici (.env présent). Mise à jour : ./scripts/deploy.sh"
command -v docker >/dev/null || die "Docker est requis : https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 est requis (paquet docker-compose-plugin)."
command -v openssl >/dev/null || die "openssl est requis."
docker info >/dev/null 2>&1 || die "Impossible de joindre Docker : lancez ce script avec un compte du groupe « docker »."

say "Installation de CyberLearning"
ask DOMAIN "Nom de domaine ou adresse IP par lequel les utilisateurs accéderont au site" "$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$DOMAIN" ] || die "Le nom de domaine ou l'adresse IP est obligatoire (--domaine)."
IS_IP=0; [[ "$DOMAIN" =~ ^[0-9.]+$|: ]] && IS_IP=1
if [ -z "$TLS" ]; then
  if [ "$IS_IP" = 1 ]; then TLS="autosigne"; else TLS="letsencrypt"; fi
  ask TLS "Certificat : letsencrypt (domaine public), autosigne (réseau interne) ou fourni (nginx/certs/)" "$TLS"
fi
case "$TLS" in letsencrypt|autosigne|fourni) ;; *) die "--tls : letsencrypt, autosigne ou fourni" ;; esac
[ "$TLS" = letsencrypt ] && [ "$IS_IP" = 1 ] && die "Let's Encrypt demande un nom de domaine public, pas une adresse IP."

ask NAME "Nom affiché de la plateforme" "CyberLearning"
ask ORG "Organisation qui exploite l'instance (raison sociale, pour les mentions légales)" ""
ask CONTACT "Adresse e-mail de contact (mentions légales, données personnelles)" ""
ask HOSTING "Hébergeur (cloud : nom et adresse ; sur site : « serveurs de l'organisation »)" ""
ask ADMIN "Pseudo du compte administrateur" "admin"
ask ADMIN_EMAIL "Adresse e-mail de l'administrateur (Let's Encrypt, invitation)" "$CONTACT"
[ "$TLS" = letsencrypt ] && [ -z "$ADMIN_EMAIL" ] && die "Let's Encrypt demande une adresse e-mail (--email-admin)."
if [ "$INTERACTIVE" = 1 ] && [ -z "$SMTP_HOST" ]; then
  ask SMTP_HOST "Serveur d'envoi d'e-mails SMTP (vide : pas d'e-mails, liens d'invitation à transmettre à la main)" ""
  if [ -n "$SMTP_HOST" ]; then
    ask SMTP_PORT "Port SMTP" "587"; ask SMTP_USER "Utilisateur SMTP" ""
    read -r -s -p "Mot de passe SMTP : " SMTP_PASSWORD; echo
    ask SMTP_FROM "Expéditeur" "$NAME <no-reply@$DOMAIN>"
  fi
fi

# --- IA locale : détection de la carte graphique ------------------------------------------------------------
GPU=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 \
   && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then GPU=1; fi
case "$AI" in
  auto) if [ "$GPU" = 1 ]; then AI=oui; say "Carte NVIDIA détectée : IA locale activée."; else AI=non; warn "Pas de carte NVIDIA utilisable par Docker : installation sans IA (activable plus tard, voir docs/installation.md, § 11)."; fi ;;
  oui) [ "$GPU" = 1 ] || warn "IA demandée mais aucune carte NVIDIA utilisable par Docker n'a été détectée : les services d'IA risquent de ne pas démarrer." ;;
  non) ;;
  *) die "--ia : auto, oui ou non" ;;
esac
ask CONTENT "Pack de contenus de l'éditeur (fichier .tar ou adresse https du canal ; vide : aucun)" ""
[ -z "$CONTENT" ] && [ -x scripts/importer-anssi.sh ] && ask IMPORT "Sans pack : importer les guides, référentiels, certifications et QCM depuis l'ANSSI (Internet requis) ? oui/non" "oui"
ask CRON "Planifier la sauvegarde chiffrée (3 h) et la purge des données (4 h) dans la crontab de ce compte ? oui/non" "oui"

# --- Configuration ------------------------------------------------------------------------------------------
say "Création des secrets et de la configuration (.env, .env.db, droits 600)"
umask 077
PROFILES=""; [ "$AI" = oui ] && PROFILES="ia"; [ "$TLS" = letsencrypt ] && PROFILES="${PROFILES:+$PROFILES,}letsencrypt"
URL="https://$DOMAIN"; [ "${HTTPS_PORT:-443}" = 443 ] || URL="$URL:$HTTPS_PORT"
cat > .env <<EOF
# Instance CyberLearning — produit par scripts/install.sh le $(date +%d/%m/%Y). Ne jamais commiter ce fichier.
COMPOSE_PROFILES=$PROFILES

# --- Django ---
DJANGO_SECRET_KEY=$(secret 64 80)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=$URL
DJANGO_ADMIN_URL_PATH=gestion-$(secret 12 10 | tr 'A-Z' 'a-z')
DJANGO_HSTS_SECONDS=0
SIGNUP_ENABLED=0
MFA_REQUIRED_FOR_PRIVILEGED=1
PRIVILEGED_IDLE_MINUTES=30
AI_ENABLED=$([ "$AI" = oui ] && echo 1 || echo 0)

# --- Instance ---
INSTANCE_NAME=$NAME
ORGANISATION_NAME=$ORG
ORGANISATION_ADDRESS=
ORGANISATION_ID=
CONTACT_EMAIL=$CONTACT
DPO_EMAIL=
HOSTING_PROVIDER=$HOSTING
THIRD_PARTY_EXAM_OUTLINES=1

# --- E-mails (vide : liens d'invitation à transmettre à la main) ---
EMAIL_HOST=$SMTP_HOST
EMAIL_PORT=$SMTP_PORT
EMAIL_HOST_USER=$SMTP_USER
EMAIL_HOST_PASSWORD=$SMTP_PASSWORD
DEFAULT_FROM_EMAIL=$SMTP_FROM

# --- Base de données : l'application utilise un rôle sans droits d'administration ---
POSTGRES_DB=cyberlearning
POSTGRES_USER=cyberlearning
DB_USER=cyberlearning_app
DB_PASSWORD=$(secret 32 40)

# --- nginx ---
SERVER_NAME=$DOMAIN
HTTP_PORT=${HTTP_PORT:-80}
HTTPS_PORT=${HTTPS_PORT:-443}
SSL_CERTIFICATE=/etc/nginx/certs/fullchain.pem
SSL_CERTIFICATE_KEY=/etc/nginx/certs/privkey.pem
HSTS_VALUE=
# Réseau autorisé pour l'administration et la gestion des comptes (ex. 10.0.0.0/8) ; all : partout
ADMIN_ALLOW_FROM=all

# --- Mises à jour des contenus : canal de l'éditeur (vide : packs installés à la main) ---
CONTENT_UPDATE_URL=$([[ "$CONTENT" =~ ^https:// ]] && echo "$CONTENT")

# --- Sauvegardes ---
BACKUP_KEEP_DAYS=14
# BACKUP_REMOTE=sauvegarde@nas:/sauvegardes/cyberlearning
EOF
DB_PASSWORD_VALUE="$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)"
cat > .env.db <<EOF
# Lu par le seul service « db » : mot de passe du super-utilisateur PostgreSQL, et rôle applicatif à créer.
POSTGRES_PASSWORD=$(secret 32 40)
DB_USER=cyberlearning_app
DB_PASSWORD=$DB_PASSWORD_VALUE
EOF
chmod 600 .env .env.db
[ -s .backup-key ] || secret 48 64 > .backup-key
chmod 600 .backup-key
umask 022

set_env() { sed -i "s|^$1=.*|$1=$2|" .env; }

# --- Certificat ---------------------------------------------------------------------------------------------
if [ "$TLS" = fourni ]; then
  [ -s nginx/certs/fullchain.pem ] && [ -s nginx/certs/privkey.pem ] \
    || die "Déposez fullchain.pem et privkey.pem dans nginx/certs/ avant de relancer (puis supprimez .env)."
else
  say "Certificat autosigné provisoire (remplacé par Let's Encrypt si demandé)"
  ./scripts/gen-selfsigned-cert.sh "$DOMAIN" >/dev/null
fi

# --- Construction et démarrage ------------------------------------------------------------------------------
if [ "$BUILD" = 1 ]; then
  say "Construction des images (plusieurs minutes la première fois)"
  docker compose build --pull
else
  say "Images fournies : pas de construction ni de téléchargement"
fi
say "Démarrage des services"
docker compose up -d

say "Attente de l'application"
for _ in $(seq 1 60); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q web)" 2>/dev/null || true)"
  [ "$state" = healthy ] && break
  sleep 5
done
[ "$state" = healthy ] || die "L'application ne démarre pas : docker compose logs web"

if [ "$TLS" = letsencrypt ]; then
  say "Demande du certificat Let's Encrypt pour $DOMAIN (le port 80 doit être joignable depuis Internet)"
  if docker compose run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/acme -d "$DOMAIN" \
       --email "$ADMIN_EMAIL" --agree-tos --non-interactive --no-eff-email \
       --deploy-hook "chgrp -R 101 /etc/letsencrypt/live /etc/letsencrypt/archive && chmod -R g+rX /etc/letsencrypt/live /etc/letsencrypt/archive"; then
    set_env SSL_CERTIFICATE "/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    set_env SSL_CERTIFICATE_KEY "/etc/letsencrypt/live/$DOMAIN/privkey.pem"
    set_env HSTS_VALUE "max-age=31536000"
    set_env DJANGO_HSTS_SECONDS 31536000
    docker compose up -d --force-recreate web nginx
  else
    warn "Échec de Let's Encrypt (DNS, pare-feu ?) : le certificat autosigné reste en place. Relance : voir docs/installation.md, § 12.1."
  fi
fi

docker compose exec -T web python manage.py check --deploy --fail-level ERROR >/dev/null 2>&1 \
  || die "Contrôle de sécurité Django en échec : docker compose exec web python manage.py check --deploy"

# --- Contenus et IA -----------------------------------------------------------------------------------------
if [ -n "$CONTENT" ]; then
  say "Installation des contenus de l'éditeur (guides, QCM, lexiques, cours, podcasts, versions audio)"
  ./scripts/mettre-a-jour-contenus.sh "$CONTENT" --sans-sauvegarde
elif [ "$IMPORT" = oui ] && [ -x scripts/importer-anssi.sh ]; then
  say "Import des contenus de l'ANSSI (le service « news » est le seul à pouvoir sortir sur Internet)"
  ./scripts/importer-anssi.sh
else
  warn "Aucun pack de contenus : catalogue vide, à remplir depuis l'atelier (/atelier/)."
fi
if [ "$AI" = oui ]; then
  say "Téléchargement des modèles d'IA (≈ 17 Go)"
  MODEL="$(grep -E '^QUIZ_LLM_MODEL=' .env | cut -d= -f2- || true)"; MODEL="${MODEL:-gemma3:12b}"
  JUDGE="$(grep -E '^PREREVIEW_LLM_MODEL=' .env | cut -d= -f2- || true)"; JUDGE="${JUDGE:-qwen3:14b}"
  for M in "$MODEL" "$JUDGE"; do
    docker compose exec -T ollama ollama pull "$M" \
      || warn "Téléchargement du modèle impossible pour l'instant : docker compose exec ollama ollama pull $M"
  done
fi

# --- Tâches planifiées --------------------------------------------------------------------------------------
if [ "$CRON" = oui ] && command -v crontab >/dev/null; then
  here="$(pwd)"
  { crontab -l 2>/dev/null | grep -v "cyberlearning-tache" || true
    echo "0 3 * * * cd $here && ./scripts/backup.sh >> backups/backup.log 2>&1 # cyberlearning-tache"
    echo "0 4 * * * cd $here && docker compose exec -T web python manage.py purge_donnees >/dev/null 2>&1 # cyberlearning-tache"
    if grep -q '^CONTENT_UPDATE_URL=https' .env; then
      echo "30 4 * * 0 cd $here && ./scripts/mettre-a-jour-contenus.sh >> backups/contenus.log 2>&1 # cyberlearning-tache"
    fi
  } | crontab -
  mkdir -p backups
fi

# --- Compte administrateur ----------------------------------------------------------------------------------
LINK="$(docker compose exec -T web python manage.py creer_admin "$ADMIN" --url "$URL" --email "$ADMIN_EMAIL" 2>/dev/null | tail -n1)"

cat <<EOF

$(printf '\033[1;32m')Installation terminée.$(printf '\033[0m')

  Adresse            $URL
  Administrateur     $ADMIN — choisissez son mot de passe ici (valable 3 jours, un seul usage) :
                     $LINK
                     La double authentification sera demandée à la première connexion.
  Administration     $URL/$(grep '^DJANGO_ADMIN_URL_PATH=' .env | cut -d= -f2-)/
  IA locale          $([ "$AI" = oui ] && echo "activée" || echo "désactivée (AI_ENABLED=0)")
  Certificat         $TLS
  Contenus           $(docker compose exec -T web python manage.py importer_contenus --version-installee 2>/dev/null | tr -d '
' | sed 's/^$/aucun pack/')

À faire maintenant :
  1. Copiez .backup-key dans un coffre-fort, hors de cette machine : sans elle, les sauvegardes sont illisibles.
  2. Complétez ORGANISATION_ADDRESS, ORGANISATION_ID et DPO_EMAIL dans .env (pages légales), puis
     docker compose up -d web ; faites relire les pages légales par un juriste.
  3. Pare-feu : n'ouvrez que les ports 80 et 443 (et SSH pour l'administration du serveur).
EOF
