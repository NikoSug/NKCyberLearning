"""
« Ma lecture » : la liste de documents (guides, référentiels) choisis par l'utilisateur.

Toutes les étapes du cycle de lecture passent par ce module. Le futur module
de test de compréhension se branchera sur `mark_finished` : une lecture
déclarée pourra alors déclencher le test avant d'être considérée comme validée.
"""
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Subquery
from django.utils import timezone

from .models import Guide, ReadingListItem, Referential


def _lookup(document):
    if isinstance(document, Guide):
        return {"guide": document}
    if isinstance(document, Referential):
        return {"referential": document}
    raise TypeError("Seuls les guides et les référentiels peuvent être ajoutés à la lecture.")


def get_item(user, document):
    return ReadingListItem.objects.filter(user=user, **_lookup(document)).first()


@transaction.atomic
def add(user, document):
    """Ajoute le document en fin de liste ; renvoie (élément, créé)."""
    item = get_item(user, document)
    if item:
        return item, False
    last = ReadingListItem.objects.filter(user=user).aggregate(last=Max("position"))["last"]
    item = ReadingListItem.objects.create(user=user, position=(last or 0) + 1, **_lookup(document))
    return item, True


def mark_started(user, document):
    """Première ouverture d'un document de la liste."""
    ReadingListItem.objects.filter(user=user, started_at__isnull=True, **_lookup(document)) \
        .update(started_at=timezone.now())


def mark_finished(user, document):
    """
    L'utilisateur déclare avoir lu le document (ajouté à sa liste s'il n'y était pas).
    Point d'entrée du futur module de test : c'est ici qu'il sera proposé.
    """
    item, _ = add(user, document)
    if not item.finished_at:
        now = timezone.now()
        item.started_at = item.started_at or now
        item.finished_at = now
        item.save(update_fields=["started_at", "finished_at"])
    return item


def mark_unfinished(item):
    item.finished_at = None
    item.save(update_fields=["finished_at"])


@transaction.atomic
def move(item, offset):
    """Échange l'élément avec son voisin dans la liste « à lire » (offset -1 ou +1)."""
    queue = list(ReadingListItem.objects.select_for_update()
                 .filter(user=item.user, finished_at__isnull=True).order_by("position", "added_at"))
    index = next((i for i, other in enumerate(queue) if other.pk == item.pk), None)
    if index is None or not 0 <= index + offset < len(queue):
        return
    queue[index], queue[index + offset] = queue[index + offset], queue[index]
    for position, other in enumerate(queue, start=1):
        if other.position != position:
            other.position = position
            other.save(update_fields=["position"])


def queue(user):
    """Documents restant à lire, dans l'ordre choisi par l'utilisateur."""
    return list(ReadingListItem.objects.filter(user=user, finished_at__isnull=True)
                .select_related("guide", "referential").order_by("position", "added_at"))


def annotate(queryset, user, field):
    """Ajoute `in_list` et `finished_at` (lecture de l'utilisateur) à une liste de guides ou de
    référentiels."""
    items = ReadingListItem.objects.filter(user=user, **{field: OuterRef("pk")})
    return queryset.annotate(in_list=Exists(items),
                             finished_at=Subquery(items.values("finished_at")[:1]))
