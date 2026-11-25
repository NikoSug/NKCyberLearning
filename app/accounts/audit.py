"""
Journal d'audit : qui a fait quoi, quand, depuis quelle adresse. Chaque événement est aussi écrit
dans
les journaux du conteneur (logger « audit »), pour être collecté hors de la machine.
"""

import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from config.client_ip import get_client_ip

from .models import AuditEvent

logger = logging.getLogger("audit")

# Actions enregistrées (libellés lisibles dans l'administration).
LABELS = {
    "connexion": "Connexion",
    "connexion_echec": "Échec de connexion",
    "deconnexion": "Déconnexion",
    "deconnexion_inactivite": "Déconnexion après inactivité",
    "2fa_activee": "Double authentification activée",
    "2fa_desactivee": "Double authentification désactivée",
    "2fa_echec": "Code de double authentification refusé",
    "2fa_secours": "Code de secours utilisé",
    "2fa_codes_regeneres": "Codes de secours régénérés",
    "role_donne": "Rôle donné",
    "role_retire": "Rôle retiré",
    "compte_desactive": "Compte désactivé",
    "compte_reactive": "Compte réactivé",
    "compte_supprime": "Compte supprimé par son titulaire",
    "compte_cree": "Compte créé par un administrateur",
    "lien_mot_de_passe": "Lien d'invitation ou de mot de passe créé",
    "donnees_exportees": "Données personnelles exportées",
    "publication": "Publication",
    "renvoi": "Renvoi au rédacteur",
    "suppression": "Suppression",
    "generation": "Pilotage des générations",
    "mot_de_passe": "Mot de passe changé",
}


def record(request, action, target="", user=None, **detail):
    """Enregistre un événement ; `user` par défaut : l'utilisateur connecté de la requête."""
    if user is None and request is not None and getattr(request, "user", None) is not None \
            and request.user.is_authenticated:
        user = request.user
    ip = get_client_ip(request) if request is not None else ""
    event = AuditEvent.objects.create(
        user=user,
        username=getattr(user, "username", "") or detail.pop("username", ""),
        action=action,
        target=str(target)[:255],
        detail=detail,
        ip=ip or None,
    )
    logger.info(
        "%s user=%s target=%s ip=%s detail=%s",
        action,
        event.username or "-",
        event.target or "-",
        ip or "-",
        detail or {},
    )
    return event


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    record(request, "connexion", user=user)


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        record(request, "deconnexion", user=user)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    # Le pseudo tenté est gardé (utile pour repérer une attaque) ; jamais le mot de passe.
    record(request, "connexion_echec", username=str(credentials.get("username", ""))[:150])
