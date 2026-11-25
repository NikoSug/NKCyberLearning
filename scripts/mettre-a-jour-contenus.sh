#!/usr/bin/env bash
# Installe ou met à jour les contenus (guides, QCM, lexiques, cours, podcasts, versions audio…) à partir d'un pack.
# Les comptes, la progression et les lectures ne sont jamais touchés ; un contenu modifié sur cette instance
# n'est pas écrasé (sauf --forcer) ; rien n'est supprimé.
#
#   ./scripts/mettre-a-jour-contenus.sh cyberlearning-contenus-2026.10.tar      pack reçu (fichier)
#   ./scripts/mettre-a-jour-contenus.sh https://maj.exemple.fr/contenus/        canal : dernière version publiée
#   ./scripts/mettre-a-jour-contenus.sh                                         canal de .env (CONTENT_UPDATE_URL)
# Options : --essai (vérifier et afficher le bilan sans rien changer), --forcer, --sans-sauvegarde.
#
# Signature : si contenus.pub est présent (livré avec l'application), le pack doit être accompagné de sa
# signature (<pack>.sig) faite par l'éditeur : un pack non signé ou modifié est refusé.
set -euo pipefail
cd "$(dirname "$0")/.."

SOURCE=""; OPTIONS=(); BACKUP=1
for arg in "$@"; do
  case "$arg" in
    --essai) OPTIONS+=(--essai); BACKUP=0 ;;
    --forcer) OPTIONS+=(--forcer) ;;
    --sans-sauvegarde) BACKUP=0 ;;
    -*) echo "Option inconnue : $arg" >&2; exit 1 ;;
    *) SOURCE="$arg" ;;
  esac
done
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
SOURCE="${SOURCE:-$(env_value CONTENT_UPDATE_URL)}"
[ -n "$SOURCE" ] || { echo "Indiquez un pack (fichier ou adresse), ou CONTENT_UPDATE_URL dans .env." >&2; exit 1; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
installed="$(docker compose exec -T web python manage.py importer_contenus --version-installee 2>/dev/null | tr -d '\r')"

if [[ "$SOURCE" =~ ^https:// ]]; then
  # Canal de mise à jour : <adresse>/version.txt donne la dernière version, le pack et sa signature sont à côté.
  BASE="${SOURCE%/}"
  latest="$(curl -fsSL --max-time 30 "$BASE/version.txt" | tr -d '[:space:]')"
  [ -n "$latest" ] || { echo "Canal de mise à jour illisible : $BASE/version.txt" >&2; exit 1; }
  if [ "$latest" = "$installed" ]; then
    echo "Contenus à jour (version $installed)."
    exit 0
  fi
  echo "==> Téléchargement des contenus $latest (installés : ${installed:-aucun})"
  PACK="$WORK/cyberlearning-contenus-$latest.tar"
  curl -fL --retry 3 -o "$PACK" "$BASE/cyberlearning-contenus-$latest.tar"
  curl -fsSL -o "$PACK.sig" "$BASE/cyberlearning-contenus-$latest.tar.sig" || true
else
  PACK="$SOURCE"
  [ -f "$PACK" ] || { echo "Pack introuvable : $PACK" >&2; exit 1; }
fi

if [ -f contenus.pub ]; then
  [ -f "$PACK.sig" ] || { echo "Signature absente ($PACK.sig) : pack refusé." >&2; exit 1; }
  openssl dgst -sha256 -verify contenus.pub -signature "$PACK.sig" "$PACK" >/dev/null \
    || { echo "Signature invalide : pack refusé (modifié ou d'une autre origine)." >&2; exit 1; }
  echo "==> Signature de l'éditeur vérifiée"
else
  echo "[!] Pas de clé publique (contenus.pub) : l'origine du pack n'est pas vérifiée, seule son intégrité l'est."
fi

if [ "$BACKUP" = 1 ] && [ -n "$installed$(docker compose exec -T web python manage.py shell -c 'from catalogue.models import Guide; print(Guide.objects.exists() or "")' 2>/dev/null | tr -d '\r')" ] && [ -s .backup-key ]; then
  echo "==> Sauvegarde chiffrée avant la mise à jour"
  ./scripts/backup.sh
fi

echo "==> Import (les fichiers sont vérifiés un par un)"
docker compose exec -T web python manage.py importer_contenus - "${OPTIONS[@]}" < "$PACK"
