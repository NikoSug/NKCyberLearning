"""
Qui peut voir et modifier un cours ou une série de podcasts en cours d'écriture ou de relecture.

  - le rédacteur : ses propres contenus, tant qu'ils sont « en rédaction » ;
  - le gestionnaire de contenu : tout, en rédaction, en relecture et une fois publié (comme pour les
    autres contenus du catalogue : ses modifications sont visibles tout de suite).
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

WRITING, REVIEW, PUBLISHED = "redaction", "a_relire", "publie"


def author_required(view):
    """Rédacteurs, gestionnaires de contenu et administrateurs."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_author:
            raise PermissionDenied("Réservé aux rédacteurs.")
        return view(request, *args, **kwargs)
    return wrapper


def is_mine(user, item):
    return item.author_id is not None and item.author_id == user.pk


def can_view(user, item):
    return user.is_content_manager or is_mine(user, item)


def can_edit(user, item):
    if user.is_content_manager:
        return item.status in (WRITING, REVIEW, PUBLISHED)
    return is_mine(user, item) and item.status == WRITING


def check_view(user, item):
    if not can_view(user, item):
        raise PermissionDenied("Ce contenu appartient à un autre rédacteur.")
