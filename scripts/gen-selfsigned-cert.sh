#!/usr/bin/env bash
# Génère un certificat auto-signé pour un usage en réseau local.
# Usage : ./scripts/gen-selfsigned-cert.sh 192.168.1.50   (ou un nom : cyber.lan)
# Pour une exposition sur Internet, utilise plutôt un vrai certificat (Let's Encrypt).
set -euo pipefail

HOST="${1:?Usage : $0 <adresse-ip-ou-nom-du-serveur>}"
DIR="$(dirname "$0")/../nginx/certs"
mkdir -p "$DIR"

if [[ "$HOST" =~ ^[0-9.]+$ ]]; then SAN="IP:$HOST"; else SAN="DNS:$HOST"; fi

openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -days 825 -subj "/CN=$HOST" -addext "subjectAltName=$SAN" \
  -keyout "$DIR/privkey.pem" -out "$DIR/fullchain.pem"

chmod 644 "$DIR/fullchain.pem"
chmod 640 "$DIR/privkey.pem"
# L'utilisateur de nginx-unprivileged (uid 101) doit pouvoir lire la clé.
chgrp 101 "$DIR/privkey.pem" 2>/dev/null || chmod 644 "$DIR/privkey.pem"

echo "Certificat créé pour $HOST dans nginx/certs/ (valide 825 jours)."
