import catalogue.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GuideCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True, verbose_name="nom")),
                ("slug", models.SlugField(max_length=140, unique=True)),
            ],
            options={
                "verbose_name": "catégorie de guide",
                "verbose_name_plural": "catégories de guides",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Certification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, verbose_name="nom")),
                ("acronym", models.CharField(blank=True, max_length=40, verbose_name="sigle")),
                ("slug", models.SlugField(max_length=280, unique=True)),
                ("kind", models.CharField(
                    choices=[
                        ("product_cert", "Certification de produit"),
                        ("product_qual", "Qualification de produit"),
                        ("provider_qual", "Qualification de prestataire"),
                        ("individual", "Certification de personnes"),
                        ("other", "Autre"),
                    ],
                    default="other", max_length=20, verbose_name="type")),
                ("description", models.TextField(blank=True, verbose_name="description")),
                ("source_url", models.URLField(blank=True, max_length=500, verbose_name="page officielle ANSSI")),
                ("is_published", models.BooleanField(default=True, verbose_name="visible sur la plateforme")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="ajoutée le")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="modifiée le")),
            ],
            options={
                "verbose_name": "certification ANSSI",
                "verbose_name_plural": "certifications ANSSI",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Guide",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255, verbose_name="titre")),
                ("slug", models.SlugField(max_length=280, unique=True)),
                ("summary", models.TextField(blank=True, verbose_name="résumé")),
                ("version", models.CharField(blank=True, max_length=50, verbose_name="version")),
                ("published_on", models.DateField(blank=True, null=True, verbose_name="date de publication ANSSI")),
                ("source_url", models.URLField(blank=True, max_length=500, verbose_name="page officielle ANSSI")),
                ("pdf", models.FileField(
                    blank=True, upload_to="guides/%Y/",
                    validators=[catalogue.validators.validate_pdf], verbose_name="fichier PDF")),
                ("is_published", models.BooleanField(default=True, verbose_name="visible sur la plateforme")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="ajouté le")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="modifié le")),
                ("category", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="guides", to="catalogue.guidecategory", verbose_name="catégorie")),
            ],
            options={
                "verbose_name": "guide ANSSI",
                "verbose_name_plural": "guides ANSSI",
                "ordering": ["title"],
            },
        ),
        migrations.CreateModel(
            name="ReadingRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("read_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="lu le")),
                ("guide", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="reading_records",
                    to="catalogue.guide", verbose_name="guide")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="reading_records",
                    to=settings.AUTH_USER_MODEL, verbose_name="utilisateur")),
            ],
            options={
                "verbose_name": "lecture",
                "verbose_name_plural": "lectures",
                "ordering": ["-read_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "guide"), name="unique_reading_per_user_guide"),
                ],
            },
        ),
    ]
