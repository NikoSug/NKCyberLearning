import django.core.validators
from django.db import migrations, models

DAILY_CHOICES = [15, 30, 45, 60, 90, 120, 180, 240]


def weekly_to_daily(apps, schema_editor):
    """Le temps hebdomadaire déjà saisi devient le temps quotidien le plus proche."""
    StudyPlan = apps.get_model("catalogue", "StudyPlan")
    for plan in StudyPlan.objects.all():
        per_day = plan.minutes_per_day / 7
        plan.minutes_per_day = min(DAILY_CHOICES, key=lambda choice: abs(choice - per_day))
        plan.save(update_fields=["minutes_per_day"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalogue", "0004_ma_lecture"),
    ]

    operations = [
        migrations.RenameField(model_name="studyplan", old_name="minutes_per_week", new_name="minutes_per_day"),
        migrations.RunPython(weekly_to_daily, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="studyplan",
            name="minutes_per_day",
            field=models.PositiveIntegerField(
                default=30, verbose_name="temps disponible par jour (min)",
                validators=[django.core.validators.MinValueValidator(5),
                            django.core.validators.MaxValueValidator(600)]),
        ),
    ]
