"""Réglages de l'instance utiles à tous les gabarits (nom affiché, éditeur, contacts)."""
from django.conf import settings


def instance(request):
    return {
        "instance_name": settings.INSTANCE_NAME,
        "organisation_name": settings.ORGANISATION_NAME,
        "contact_email": settings.CONTACT_EMAIL,
        "email_enabled": settings.EMAIL_ENABLED,
        "ai_enabled": settings.AI_ENABLED,
        "app_version": settings.APP_VERSION,
    }
