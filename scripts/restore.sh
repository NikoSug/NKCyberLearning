#!/usr/bin/env bash
# Restauration d'une sauvegarde chiffrée (base et fichiers).
# Usage : ./scripts/restore.sh backups/db-AAAAMMJJ-HHMMSS.dump.enc [backups/media-AAAAMMJJ-HHMMSS.tar.gz.enc]
# La base actuelle est remplacée (--clean) : à faire de préférence sur une instance arrêtée aux utilisateurs.
# À tester régulièrement (par exemple chaque mois, sur une machine de test) : une sauvegarde jamais restaurée
# n'est pas une sauvegarde.
set -euo pipefail

cd "$(dirname "$0")/.."
DB_FILE="${1:?Usage : $0 <db-….dump.enc> [media-….tar.gz.enc]}"
MEDIA_FILE="${2:-}"
KEY_FILE="${BACKUP_KEY_FILE:-.backup-key}"
[ -s "$KEY_FILE" ] || { echo "Erreur : clé $KEY_FILE absente (BACKUP_KEY_FILE pour en indiquer une autre)." >&2; exit 1; }
decrypt() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$KEY_FILE" -in "$1"; }

read -r -p "Remplacer la base actuelle par $DB_FILE ? Tapez « oui » : " answer
[ "$answer" = "oui" ] || { echo "Annulé."; exit 1; }

echo "==> Arrêt de l'application"
docker compose stop web nginx

echo "==> Base de données"
decrypt "$DB_FILE" | docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --role="${DB_USER:-$POSTGRES_USER}"'

if [ -n "$MEDIA_FILE" ]; then
  echo "==> Fichiers"
  # Le conteneur web est en lecture seule sauf /app/media : on restaure dans le volume par un conteneur jetable.
  decrypt "$MEDIA_FILE" | docker compose run --rm -T --no-deps --entrypoint "" web tar -C /app -xzf -
fi

echo "==> Redémarrage"
docker compose up -d web nginx
echo "Restauration terminée."
