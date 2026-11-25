#!/bin/sh
# Premier démarrage de la base uniquement (volume vide) : crée le rôle avec lequel l'application se connecte.
# Il possède la base (tables, migrations, extensions « de confiance » comme unaccent et pg_trgm) mais n'est pas
# super-utilisateur : une injection SQL ne donnerait accès ni aux autres bases, ni aux fichiers, ni aux commandes
# du serveur (COPY … PROGRAM). Le super-utilisateur (POSTGRES_USER) ne sert qu'à l'administration et aux sauvegardes.
set -eu

if [ -z "${DB_USER:-}" ] || [ -z "${DB_PASSWORD:-}" ]; then
  echo "[db-init] DB_USER / DB_PASSWORD absents : l'application utilisera le super-utilisateur (installation ancienne)."
  exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$DB_USER" -v app_password="$DB_PASSWORD" -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER DATABASE :"db" OWNER TO :"app_user";
ALTER SCHEMA public OWNER TO :"app_user";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"app_user";
SQL
echo "[db-init] Rôle applicatif « $DB_USER » créé."
