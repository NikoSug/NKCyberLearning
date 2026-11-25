from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes"

    def ready(self):
        # Imports pour leurs effets : signaux du journal d'audit, contrôles de déploiement.
        from . import audit, checks  # noqa: F401  # pylint: disable=unused-import
