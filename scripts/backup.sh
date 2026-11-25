#!/usr/bin/env bash
# Sauvegarde chiffrée de la base PostgreSQL et des fichiers téléversés (PDF, enregistrements, audio).
# Usage : ./scripts/backup.sh   (planifiée chaque nuit par install.sh, voir docs/installation.md)
#
# Chiffrement AES-256 (openssl, dérivation PBKDF2) avec la clé de .backup-key, créée à l'installation.
# Sans cette clé, les sauvegardes sont illisibles : gardez-en une copie hors de la machine (coffre-fort).
# BACKUP_REMOTE (facultatif, dans .env) : destination rsync des copies, ex. sauvegarde@nas:/sauvegardes/cyber
set -euo pipefail

cd "$(dirname "$0")/.."
umask 077   # sauvegardes lisibles uniquement par leur propriétaire

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
KEEP_DAYS="$(env_value BACKUP_KEEP_DAYS)"; KEEP_DAYS="${KEEP_DAYS:-14}"
DEST="$(env_value BACKUP_DIR)"; DEST="${DEST:-./backups}"
REMOTE="$(env_value BACKUP_REMOTE)"
KEY_FILE=".backup-key"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

if [ ! -s "$KEY_FILE" ]; then
  echo "Erreur : clé de chiffrement $KEY_FILE absente (créée par install.sh ; sinon : openssl rand -base64 48 > $KEY_FILE)." >&2
  exit 1
fi
encrypt() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$KEY_FILE"; }

echo "==> Base de données"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  | encrypt > "$DEST/db-$STAMP.dump.enc"

echo "==> Fichiers téléversés et audio"
docker compose exec -T web tar -C /app -czf - media | encrypt > "$DEST/media-$STAMP.tar.gz.enc"

echo "==> Suppression des sauvegardes de plus de $KEEP_DAYS jours"
find "$DEST" -type f \( -name 'db-*.dump*' -o -name 'media-*.tar.gz*' \) -mtime +"$KEEP_DAYS" -delete

if [ -n "$REMOTE" ]; then
  echo "==> Copie vers $REMOTE"
  rsync -a --partial "$DEST/db-$STAMP.dump.enc" "$DEST/media-$STAMP.tar.gz.enc" "$REMOTE/"
fi

ls -lh "$DEST" | tail -n 4
echo "Sauvegarde chiffrée terminée.${REMOTE:+ Copie envoyée.}${REMOTE:- Pensez à copier ce dossier hors de la machine (BACKUP_REMOTE).}"
