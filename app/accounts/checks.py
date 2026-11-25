"""Contrôles bloquants avant une mise en production (« manage.py check --deploy », lancé par
deploy.sh et la CI)."""

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def strong_secret_key(app_configs, **kwargs):
    """Django ne fait qu'avertir (W009) : ici, une clé faible empêche le déploiement."""
    key = settings.SECRET_KEY or ""
    if len(key) < 50 or len(set(key)) < 5 or key.startswith("django-insecure-"):
        return [
            Error(
                "DJANGO_SECRET_KEY est trop faible (50 caractères aléatoires au moins).",
                hint='python3 -c "import secrets; print(secrets.token_urlsafe(64))"',
                id="accounts.E001",
            )
        ]
    return []
