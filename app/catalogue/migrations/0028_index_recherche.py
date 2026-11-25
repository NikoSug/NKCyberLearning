"""Index trigrammes pour la recherche plein texte sans accents (voir catalogue/lookups.py)."""
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

FUNCTION = """
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;
"""

# (table, colonne) : champs parcourus par la recherche globale.
COLUMNS = [
    ("catalogue_guidesection", "text"), ("catalogue_guidesection", "title"),
    ("catalogue_glossaryentry", "term"), ("catalogue_glossaryentry", "expansion"),
    ("catalogue_glossaryentry", "definition"),
]


def index_sql(columns):
    create = [f'CREATE INDEX IF NOT EXISTS {t}_{c}_trgm ON {t} USING gin (UPPER(f_unaccent("{c}")) gin_trgm_ops);'
              for t, c in columns]
    drop = [f"DROP INDEX IF EXISTS {t}_{c}_trgm;" for t, c in columns]
    return create, drop


class Migration(migrations.Migration):

    dependencies = [
        ("catalogue", "0027_atelier_de_creation"),
    ]

    operations = [
        TrigramExtension(),
        migrations.RunSQL(FUNCTION, "DROP FUNCTION IF EXISTS f_unaccent(text);"),
        migrations.RunSQL(*index_sql(COLUMNS)),
    ]
