from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_content_managers_group(sender, **kwargs):
    """
    Crée (ou met à jour) le groupe "Gestionnaires de contenu" après chaque
    migration : il peut gérer guides, listes, certifications et référentiels dans
    l'administration, consulter les lectures, mais pas les comptes.
    """
    from django.contrib.auth.models import Group, Permission

    from accounts.models import AUTHORS_GROUP, CONTENT_MANAGERS_GROUP

    Group.objects.get_or_create(
        name=AUTHORS_GROUP
    )  # atelier de création : aucun droit d'administration
    group, _ = Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)
    codenames = [
        f"{action}_{model}"
        for model in ("guide", "guidecategory", "guidedocument", "guidecitation", "certification",
                      "referential", "requirement", "quizquestion")
        for action in ("add", "change", "delete", "view")
    ] + ["view_readinglistitem", "view_studyplan", "view_requirementprogress", "view_quizattempt"]
    group.permissions.set(
        Permission.objects.filter(content_type__app_label="catalogue", codename__in=codenames))


class CatalogueConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalogue"
    verbose_name = "Catalogue ANSSI"

    def ready(self):
        # Import pour ses effets : recherche sans accents (« __sans_accent »).
        from . import lookups  # noqa: F401  # pylint: disable=unused-import
        post_migrate.connect(ensure_content_managers_group, sender=self)
