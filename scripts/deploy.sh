#!/usr/bin/env bash
# Mise à jour de l'application (code) d'une instance installée par install.sh, sur site ou dans le cloud.
#
#   ./scripts/deploy.sh                                   dépôt Git : branche DEPLOY_BRANCH (main par défaut)
#   ./scripts/deploy.sh ~/packs/cyberlearning-1.1.0.tar.gz   archive livrée par l'éditeur (signature vérifiée)
#
# Sauvegarde chiffrée d'abord ; .env, .env.db, .backup-key, nginx/certs/ et les données ne sont jamais touchés.
# Voir docs/mettre-a-jour-une-instance.md.
#
# Tout le script est dans une fonction : bash la lit en entier avant de l'exécuter, ce qui permet à l'archive
# de remplacer ce fichier lui-même sans risque.
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."
  local archive="${1:-}" before

  [ -f .env ] || { echo "Erreur : instance non installée (.env absent). Première installation : ./scripts/install.sh"; exit 1; }
  for secret in .env .env.db .backup-key; do
    if [ -f "$secret" ] && [ "$(stat -c %a "$secret")" != "600" ]; then
      echo "Correction des droits de $secret (600)…"
      chmod 600 "$secret"
    fi
  done
  [ -f nginx/certs/fullchain.pem ] && [ -f nginx/certs/privkey.pem ] || {
    echo "Erreur : certificats TLS absents (nginx/certs/). Première installation : ./scripts/install.sh"
    exit 1
  }
  before="$(cat app/VERSION 2>/dev/null || echo inconnue)"

  if [ -n "$archive" ]; then
    [ -f "$archive" ] || { echo "Archive introuvable : $archive" >&2; exit 1; }
    if [ -f contenus.pub ]; then
      [ -f "$archive.sig" ] || { echo "Signature absente ($archive.sig) : archive refusée." >&2; exit 1; }
      openssl dgst -sha256 -verify contenus.pub -signature "$archive.sig" "$archive" >/dev/null \
        || { echo "Signature invalide : archive refusée (modifiée ou d'une autre origine)." >&2; exit 1; }
      echo "==> Signature de l'éditeur vérifiée"
    else
      echo "[!] Pas de clé publique (contenus.pub) : l'origine de l'archive n'est pas vérifiée."
    fi
  fi

  if [ -s .backup-key ] && [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
    echo "==> Sauvegarde chiffrée avant la mise à jour"
    ./scripts/backup.sh
  fi

  if [ -n "$archive" ]; then
    echo "==> Installation de l'archive $(basename "$archive")"
    local files
    files="$(tar -tzf "$archive" | grep -v '/$' | sort)"
    # Fichiers de la version installée absents de la nouvelle (ex. migration ajoutée par une version plus récente,
    # lors d'un retour en arrière) : supprimés, sinon ils resteraient actifs.
    if [ -f .fichiers-version ]; then
      comm -23 <(sort .fichiers-version) <(printf '%s\n' "$files") | while IFS= read -r stale; do
        [ -n "$stale" ] && rm -f -- "$stale" && echo "    retiré : $stale"
      done
    fi
    tar -xzf "$archive"
    printf '%s\n' "$files" > .fichiers-version
  elif [ -d .git ]; then
    local branch="${DEPLOY_BRANCH:-main}"
    echo "==> Récupération du code (branche $branch)"
    git fetch --prune origin
    git checkout "$branch"
    git pull --ff-only origin "$branch"   # refuse toute divergence locale
  else
    echo "Ni dépôt Git ni archive : indiquez l'archive livrée (./scripts/deploy.sh cyberlearning-X.Y.Z.tar.gz)." >&2
    exit 1
  fi
  echo "    Version : $before → $(cat app/VERSION 2>/dev/null || echo inconnue)"

  echo "==> Construction des images (avec mise à jour des images de base)"
  docker compose build --pull

  echo "==> Démarrage / redémarrage des services (migrations appliquées au démarrage)"
  docker compose up -d --remove-orphans

  echo "==> Vérification de la configuration de sécurité Django"
  docker compose exec -T web python manage.py check --deploy --fail-level ERROR

  echo "==> Nettoyage des anciennes images"
  docker image prune -f >/dev/null

  docker compose ps
  echo "Mise à jour terminée. Pensez ensuite aux contenus : ./scripts/mettre-a-jour-contenus.sh"
}

main "$@"
exit
