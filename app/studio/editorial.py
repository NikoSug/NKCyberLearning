"""
Cycle de vie d'une contribution au catalogue (guide, référentiel, programme d'examen, certification,
parcours sur mesure, version audio fournie) :

    en rédaction ──soumettre──▶ à relire ──publier──▶ publié (visible des apprenants)
         ▲                          │
         └────────renvoyer──────────┘

Un rédacteur ne voit et ne modifie que ses propres brouillons ; un gestionnaire de contenu voit
tout,
modifie aussi les contenus publiés, publie ou renvoie. Un contenu créé par un gestionnaire commence
lui
aussi en rédaction : il le publie quand il est prêt.
"""

from django.utils import timezone

from catalogue.models import Editorial

WRITING = Editorial.EditorialStatus.WRITING
REVIEW = Editorial.EditorialStatus.REVIEW
PUBLISHED = Editorial.EditorialStatus.PUBLISHED


def start(item, user):
    """Nouveau brouillon : invisible tant qu'il n'est pas publié."""
    item.author, item.editorial_status = user, WRITING
    if hasattr(item, "is_published"):
        item.is_published = False


def is_draft(item):
    return item.editorial_status in (WRITING, REVIEW)


def can_view(user, item):
    return user.is_content_manager or (item.author_id == user.pk and is_draft(item)) or \
        getattr(item, "is_published", False)


def can_edit(user, item):
    if user.is_content_manager:
        return True
    return item.author_id == user.pk and item.editorial_status == WRITING


def submit(item):
    item.editorial_status, item.submitted_at = REVIEW, timezone.now()
    item.save(update_fields=["editorial_status", "submitted_at"])


def send_back(item):
    item.editorial_status = WRITING
    item.save(update_fields=["editorial_status"])


def publish(item):
    item.editorial_status = PUBLISHED
    fields = ["editorial_status"]
    if hasattr(item, "is_published"):
        item.is_published = True
        fields.append("is_published")
    item.save(update_fields=fields)


def deletable(user, item):
    """Seul un brouillon jamais publié se supprime ici (un contenu publié se masque dans
    l'administration)."""
    return is_draft(item) and not getattr(item, "is_published", False) and \
        (user.is_content_manager or item.author_id == user.pk)
