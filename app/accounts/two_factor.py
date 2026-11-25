"""
Double authentification : obligatoire pour les comptes à droits élevés (administrateurs,
gestionnaires,
rédacteurs), facultative pour les apprenants. Après le mot de passe, un code de l'application (ou un
code
de secours) est demandé une fois par session.
"""

import hashlib

from django.db import transaction
from django.utils import timezone

from . import totp
from .models import TwoFactor

SESSION_KEY = "2fa_ok"


def device(user):
    """Double authentification active du compte, ou None."""
    return TwoFactor.objects.filter(user=user, confirmed_at__isnull=False).first()


def has_device(request):
    """Relu en base à chaque requête : activer la double authentification l'impose aussitôt aux
    autres sessions."""
    return TwoFactor.objects.filter(user=request.user, confirmed_at__isnull=False).exists()


def verified(request):
    return bool(request.session.get(SESSION_KEY))


def mark_verified(request):
    # Nouvelle clé de session une fois le second facteur passé (pas de réutilisation d'une session
    # antérieure).
    request.session.cycle_key()
    request.session[SESSION_KEY] = True


def pending(user):
    """Activation en cours (secret créé, pas encore confirmé par un premier code) : créée au
    besoin."""
    item, _ = TwoFactor.objects.get_or_create(user=user, defaults={"secret": totp.new_secret()})
    return item


def _digest(code):
    return hashlib.sha256(totp.normalize_recovery(code).encode()).hexdigest()


def confirm(item, code):
    """Premier code juste : la double authentification est active ; renvoie les codes de secours (en
    clair, une fois)."""
    counter = totp.match(item.secret, code)
    if counter is None:
        return None
    codes = totp.new_recovery_codes()
    item.confirmed_at, item.last_counter = timezone.now(), counter
    item.recovery_codes = [_digest(c) for c in codes]
    item.save()
    return codes


def regenerate(item):
    codes = totp.new_recovery_codes()
    item.recovery_codes = [_digest(c) for c in codes]
    item.save(update_fields=["recovery_codes"])
    return codes


def check(user, code):
    """
    Vérifie un code de l'application ou un code de secours. Renvoie "totp", "secours" ou None.
    Verrou sur la ligne : deux requêtes simultanées ne peuvent pas utiliser le même code.
    """
    with transaction.atomic():
        item = (
            TwoFactor.objects.select_for_update()
            .filter(user=user, confirmed_at__isnull=False)
            .first()
        )
        if item is None:
            return None
        counter = totp.match(item.secret, code, after=item.last_counter)
        if counter is not None:
            item.last_counter = counter
            item.save(update_fields=["last_counter"])
            return "totp"
        digest = _digest(code)
        if len(totp.normalize_recovery(code)) == 12 and digest in item.recovery_codes:
            item.recovery_codes = [d for d in item.recovery_codes if d != digest]
            item.save(update_fields=["recovery_codes"])
            return "secours"
    return None


def remaining_recovery(user):
    item = device(user)
    return len(item.recovery_codes) if item else 0
