#!/usr/bin/env bash
# Côté éditeur : publie une nouvelle version de l'application (code), prête à être livrée aux clients.
#
#   ./scripts/publier-version.sh 1.1.0
#
# À lancer sur main, après y avoir fusionné dev (guide de livraison de l'éditeur).
# Étapes : vérifie que le dépôt est propre et que docs/notes-de-version.md décrit la version, écrit app/VERSION, crée le commit
# « Version X » et l'étiquette vX, puis produit dans packs/ :
#   cyberlearning-X.tar.gz        archive du code (fichiers suivis par Git uniquement : aucun secret)
#   cyberlearning-X.tar.gz.sig    signature (même clé que les packs de contenus : contenus-signature.key)
#   cyberlearning-X.tar.gz.sha256 empreinte
# L'archive exclut les outils de développement et de l'éditeur (.gitattributes, export-ignore).
# Rien n'est poussé : relisez, puis « CL_PUBLICATION=1 git push origin main --tags ».
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?Usage : $0 <version>   (ex. 1.1.0)}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version attendue au format X.Y.Z (ex. 1.1.0)." >&2; exit 1; }
[ "$(git branch --show-current)" = main ] || { echo "Une version se publie depuis main : git switch main && git merge --no-ff dev" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Le dépôt a des modifications non commitées : commitez-les d'abord." >&2; exit 1; }
git rev-parse "v$VERSION" >/dev/null 2>&1 && { echo "L'étiquette v$VERSION existe déjà." >&2; exit 1; }
grep -q "^## $VERSION" docs/notes-de-version.md || { echo "Ajoutez d'abord une section « ## $VERSION » dans docs/notes-de-version.md." >&2; exit 1; }

echo "$VERSION" > app/VERSION
git add app/VERSION
git diff --cached --quiet || git commit -q -m "Version $VERSION"   # 1.0.0 : VERSION déjà à jour
git tag -a "v$VERSION" -m "Version $VERSION"

mkdir -p packs
ARCHIVE="packs/cyberlearning-$VERSION.tar.gz"
git archive --format=tar.gz -o "$ARCHIVE" "v$VERSION"
if [ -f contenus-signature.key ]; then
  openssl dgst -sha256 -sign contenus-signature.key -out "$ARCHIVE.sig" "$ARCHIVE"
  echo "==> Archive signée"
else
  echo "[!] Pas de clé de signature (contenus-signature.key) : archive non signée (./scripts/publier-contenus.sh --creer-cle)."
fi
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"
echo "Version $VERSION prête : $ARCHIVE."
echo "À faire : CL_PUBLICATION=1 git push origin main --tags ; git switch dev && git merge main && CL_PUBLICATION=1 git push ; puis livrer l'archive et sa .sig."
