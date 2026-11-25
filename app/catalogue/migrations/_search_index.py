"""SQL des index trigrammes de la recherche (partagé par les migrations ; ignoré par le chargeur : nom en « _ »)."""


def index_sql(columns):
    """(création, suppression) des index GIN sur UPPER(f_unaccent(colonne)), l'expression de « sans_accent »."""
    create = [f'CREATE INDEX IF NOT EXISTS {t}_{c}_trgm ON {t} USING gin (UPPER(f_unaccent("{c}")) gin_trgm_ops);'
              for t, c in columns]
    drop = [f"DROP INDEX IF EXISTS {t}_{c}_trgm;" for t, c in columns]
    return create, drop
