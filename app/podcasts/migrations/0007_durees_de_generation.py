from django.db import migrations, models


def backfill(apps, schema_editor):
    """Reprend ce que les horodatages permettent encore de savoir (sinon le tableau de bord affiche « — »)."""
    Series = apps.get_model("podcasts", "Series")
    for series in Series.objects.filter(started_at__isnull=False, generated_at__isnull=False):
        if series.generated_at > series.started_at:  # écriture : started_at n'a pas été remis à zéro depuis
            series.script_seconds = round((series.generated_at - series.started_at).total_seconds(), 1)
        elif series.published_at and series.published_at > series.started_at:  # enregistrement après l'écriture
            series.recording_seconds = round((series.published_at - series.started_at).total_seconds(), 1)
        else:
            continue
        series.save(update_fields=["script_seconds", "recording_seconds"])


class Migration(migrations.Migration):

    dependencies = [
        ('podcasts', '0006_episode_a_reenregistrer'),
    ]

    operations = [
        migrations.AddField(
            model_name='series',
            name='recording_seconds',
            field=models.FloatField(blank=True, null=True, verbose_name="durée d'enregistrement (s)"),
        ),
        migrations.AddField(
            model_name='series',
            name='script_seconds',
            field=models.FloatField(blank=True, null=True, verbose_name="durée d'écriture (s)"),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
