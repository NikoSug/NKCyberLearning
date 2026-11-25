"""Pages légales de l'instance, remplies avec les réglages de l'exploitant (.env) ; accessibles sans
compte."""

from django.conf import settings
from django.shortcuts import render

from accounts.management.commands.purge_donnees import SHORT_DAYS

PAGES = {
    "mentions": ("legal/mentions.html", "Mentions légales"),
    "confidentialite": ("legal/privacy.html", "Politique de confidentialité"),
    "conditions": ("legal/terms.html", "Conditions d'utilisation"),
    "credits": ("legal/credits.html", "Crédits et licences"),
}


def page(request, name):
    template, title = PAGES[name]
    return render(request, template, {
        "title": title,
        "org": {
            "name": settings.ORGANISATION_NAME, "address": settings.ORGANISATION_ADDRESS,
            "id": settings.ORGANISATION_ID, "email": settings.CONTACT_EMAIL,
            "dpo": settings.DPO_EMAIL or settings.CONTACT_EMAIL, "host": settings.HOSTING_PROVIDER,
        },
        "retention": {"audit": settings.AUDIT_RETENTION_DAYS, "short": SHORT_DAYS,
                      "session_hours": settings.SESSION_COOKIE_AGE // 3600},
        "incomplete": not (settings.ORGANISATION_NAME and settings.CONTACT_EMAIL),
    })
