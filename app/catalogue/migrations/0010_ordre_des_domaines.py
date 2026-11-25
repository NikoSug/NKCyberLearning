"""Range les domaines dans un ordre pédagogique (Fondamentaux d'abord), comme le fera désormais l'import."""
from django.db import migrations

ORDER = [
    "fondamentaux", "gouvernance-risques", "architecture-administration", "reseau", "postes-mobilite",
    "cloud-virtualisation", "developpement-ia", "cryptographie", "supervision-detection", "crise-remediation",
    "systemes-industriels",
]


def reorder(apps, schema_editor):
    GuideCategory = apps.get_model("catalogue", "GuideCategory")
    for position, slug in enumerate(ORDER):
        GuideCategory.objects.filter(slug=slug).update(position=position)


class Migration(migrations.Migration):
    dependencies = [("catalogue", "0009_alter_dailyreview_options_and_more")]
    operations = [migrations.RunPython(reorder, migrations.RunPython.noop)]
