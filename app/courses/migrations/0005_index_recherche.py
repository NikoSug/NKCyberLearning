"""Index trigrammes des leçons pour la recherche sans accents (fonction f_unaccent : catalogue 0028)."""
from django.db import migrations

from catalogue.migrations._search_index import index_sql

COLUMNS = [("courses_lesson", "title"), ("courses_lesson", "body"), ("courses_lesson", "objective")]


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0004_libelle_atelier_creation"),
        ("catalogue", "0028_index_recherche"),
    ]

    operations = [
        migrations.RunSQL(*index_sql(COLUMNS)),
    ]
