"""
Invitations et liens de (ré)initialisation du mot de passe. Un lien est valable 3 jours et ne sert
qu'une fois
(le jeton dépend du mot de passe actuel). Sans serveur d'e-mails, l'administrateur copie le lien et
le transmet.
"""

import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def set_password_link(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("accounts:password_reset_confirm", args=[uid, token]))


def send_link(user, link, new):
    """Envoie le lien par e-mail si possible ; renvoie True si l'envoi a réussi."""
    if not (settings.EMAIL_ENABLED and user.email):
        return False
    context = {"user": user, "link": link, "new": new, "instance_name": settings.INSTANCE_NAME,
               "days": settings.PASSWORD_RESET_TIMEOUT // 86400}
    subject = (f"Votre compte {settings.INSTANCE_NAME}" if new
               else f"Choisir un nouveau mot de passe — {settings.INSTANCE_NAME}")
    try:
        send_mail(
            subject, render_to_string("accounts/emails/invitation.txt", context), None, [user.email]
        )
    except OSError as exc:  # SMTPException en hérite
        logger.warning("Invitation non envoyée à %s : %s", user.username, exc)
        return False
    return True
