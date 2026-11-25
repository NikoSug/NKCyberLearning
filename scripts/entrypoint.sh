#!/bin/sh
# Point d'entrée du conteneur web : applique les migrations puis lance la commande.
set -eu

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] Application des migrations…"
  python manage.py migrate --noinput
  python manage.py createcachetable   # cache partagé (limites de débit)
  # XP des activités passées (idempotent : ne rapporte jamais deux fois).
  python manage.py backfill_xp
  # Durées de conservation des données personnelles (RGPD).
  python manage.py purge_donnees
fi

exec "$@"
