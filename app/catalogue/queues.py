"""
Files d'attente des générations (QCM, cours, podcasts, lexiques, audio) : ordre manuel et retrait.

L'ordre d'une file est celui de `Modèle.QUEUE_ORDER`, utilisé aussi par le service qui prend le
prochain élément : changer l'ordre ici change donc vraiment ce qui sera généré ensuite. Un
déplacement numérote toute la file (`queue_rank`) ; les éléments mis en file plus tard passent
après ceux classés à la main.

Retirer un élément ne perd jamais de travail déjà relu ou publié :
  - QCM, cours, lexique, série de podcasts en attente : abandonnés (ils redeviennent « à générer »)
  ;
  - série validée en attente d'enregistrement : retour en relecture ;
  - version audio en attente de régénération : l'ancienne version, si elle est complète, redevient
    écoutable ; sinon la demande est supprimée.
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count

from .models import Glossary, Narration, QuizDraft

MOVES = ("haut", "monter", "descendre", "bas")


@dataclass
class Queue:
    model: type
    waiting: tuple
    related: tuple
    label: object        # élément → titre affiché
    note: object         # élément → précision (« demandée », « à enregistrer »…) ou ""


def _queues():
    from courses.models import Course  # imports locaux : ces applications importent catalogue
    from podcasts.models import Series
    return {
        "qcm": Queue(
            QuizDraft,
            (QuizDraft.Status.PENDING,),
            ("guide", "referential"),
            lambda d: str(d.subject),
            lambda d: "questions rejetées à régénérer" if d.questions_n else "",
        ),
        "cours": Queue(
            Course,
            (Course.Status.PENDING,),
            ("category", "guide"),
            lambda c: c.title,
            lambda c: c.get_kind_display(),
        ),
        "podcast": Queue(
            Series,
            (Series.Status.TO_RECORD, Series.Status.PENDING),
            ("category",),
            lambda s: s.title,
            lambda s: (
                "validée, à enregistrer" if s.status == Series.Status.TO_RECORD else s.category.name
            ),
        ),
        "lexique": Queue(
            Glossary, (Glossary.Status.PENDING,), ("guide",), lambda g: g.guide.title, lambda g: ""
        ),
        "audio": Queue(
            Narration,
            (Narration.Status.PENDING,),
            ("guide",),
            lambda n: n.guide.title,
            lambda n: " · ".join(
                filter(
                    None,
                    [
                        "demandée par un utilisateur" if n.priority == 0 else "",
                        "régénération (nouvelle voix)" if n.version else "",
                    ],
                )
            ),
        ),
    }


def queue(kind):
    queues = _queues()
    if kind not in queues:
        raise KeyError(kind)
    return queues[kind]


def waiting(kind):
    """Éléments en attente, dans l'ordre où ils seront générés."""
    spec = queue(kind)
    items = spec.model.objects.filter(status__in=spec.waiting).select_related(*spec.related)
    if spec.model is QuizDraft:
        items = items.annotate(questions_n=Count("questions"))
    return list(items.order_by(*spec.model.QUEUE_ORDER))


def rows(kind):
    spec = queue(kind)
    return [{"item": item, "position": n, "title": spec.label(item), "note": spec.note(item),
             "progress": getattr(item, "progress", "")}
            for n, item in enumerate(waiting(kind), start=1)]


@transaction.atomic
def move(kind, pk, where):
    """Déplace un élément (« haut », « monter », « descendre », « bas ») ; renvoie sa nouvelle
    position."""
    if where not in MOVES:
        raise ValueError(where)
    order = waiting(kind)
    index = next((i for i, item in enumerate(order) if item.pk == pk), None)
    if index is None:
        return None  # pris par le service entre-temps, ou retiré
    target = {"haut": 0, "monter": max(index - 1, 0), "descendre": min(index + 1, len(order) - 1),
              "bas": len(order) - 1}[where]
    order.insert(target, order.pop(index))
    _renumber(kind, order)
    return target + 1


def _renumber(kind, order):
    model = queue(kind).model
    for rank, item in enumerate(order, start=1):
        item.queue_rank = rank
    fields = ["queue_rank"]
    if model is Narration:
        # Les demandes des utilisateurs (priorité 0) passent d'abord : un élément placé devant l'une
        # d'elles prend sa priorité, pour que l'ordre choisi soit bien celui de la file.
        lowest = None
        for item in reversed(order):
            lowest = item.priority if lowest is None else min(lowest, item.priority)
            item.priority = lowest
        fields.append("priority")
    model.objects.bulk_update(order, fields)


def remove(kind, pk):
    """Retire un élément de la file ; renvoie son titre, ou None s'il n'y était plus."""
    spec = queue(kind)
    item = (
        spec.model.objects.filter(pk=pk, status__in=spec.waiting)
        .select_related(*spec.related)
        .first()
    )
    if item is None:
        return None
    title = spec.label(item)
    _withdraw(kind, item)
    return title


def clear(kind):
    """Vide la file ; renvoie le nombre d'éléments retirés."""
    items = waiting(kind)
    for item in items:
        _withdraw(kind, item)
    return len(items)


def _withdraw(kind, item):
    item.queue_rank = None
    if kind == "audio":
        sections = list(item.sections.all())
        complete = sections and all(
            s.audio and s.audio.storage.exists(s.audio.name) for s in sections
        )
        if complete and item.version:  # régénération annulée : l'ancienne version reste écoutable
            item.status, item.priority = Narration.Status.READY, 1
            item.sections_done = item.sections_total = len(sections)
            item.save(
                update_fields=[
                    "status",
                    "priority",
                    "queue_rank",
                    "sections_done",
                    "sections_total",
                ]
            )
        else:
            for section in sections:
                section.audio.delete(save=False)
            item.delete()
        return
    if kind == "podcast" and item.status == item.Status.TO_RECORD:
        item.status, item.approved_by, item.approved_at = item.Status.REVIEW, None, None
        item.save(update_fields=["status", "approved_by", "approved_at", "queue_rank"])
        return
    item.status = item.Status.DISCARDED
    item.save(update_fields=["status", "queue_rank"])
