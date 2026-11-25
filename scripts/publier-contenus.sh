#!/usr/bin/env bash
# Côté éditeur : produit un pack de contenus signé à partir de cette instance (l'instance de référence, où les
# contenus sont générés et relus), prêt à être livré aux clients.
#
#   ./scripts/publier-contenus.sh                 version = date du jour
#   ./scripts/publier-contenus.sh 2026.11         version choisie
#   ./scripts/publier-contenus.sh --creer-cle     crée la paire de clés de signature (une seule fois)
#
# Résultat dans packs/ : cyberlearning-contenus-<version>.tar, sa signature .sig et version.txt. Ce dossier peut
# être publié tel quel sur un serveur web (HTTPS) : c'est le « canal » que les instances clientes interrogent
# (CONTENT_UPDATE_URL). La clé privée contenus-signature.key ne doit jamais quitter la machine de l'éditeur ;
# la clé publique contenus.pub est livrée avec l'application (à commiter).
#
# Compose : en développement, ajoutez -f docker-compose.yml -f docker-compose.dev.yml via COMPOSE_FILE.
set -euo pipefail
cd "$(dirname "$0")/.."

KEY="contenus-signature.key"
if [ "${1:-}" = "--creer-cle" ]; then
  [ -f "$KEY" ] && { echo "La clé $KEY existe déjà." >&2; exit 1; }
  (umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$KEY")
  openssl pkey -in "$KEY" -pubout -out contenus.pub
  echo "Clés créées : $KEY (privée, à sauvegarder dans un coffre-fort) et contenus.pub (publique, à commiter)."
  exit 0
fi

VERSION="${1:-$(date +%Y.%m.%d)}"
mkdir -p packs
PACK="packs/cyberlearning-contenus-$VERSION.tar"

echo "==> Export des contenus publiés (version $VERSION)"
docker compose exec -T web python manage.py exporter_contenus - --numero "$VERSION" > "$PACK"

if [ -f "$KEY" ]; then
  openssl dgst -sha256 -sign "$KEY" -out "$PACK.sig" "$PACK"
  echo "==> Pack signé"
else
  echo "[!] Pas de clé de signature ($KEY) : pack non signé (./scripts/publier-contenus.sh --creer-cle)."
fi
echo "$VERSION" > packs/version.txt
sha256sum "$PACK" | tee "$PACK.sha256"
echo "Prêt : $PACK — à remettre au client, ou dossier packs/ à publier sur le canal de mise à jour."
