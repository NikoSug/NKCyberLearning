"""Enregistrement d'une grille d'exigences en base, sans perdre le suivi des utilisateurs."""
import re

from django.db import transaction

from ..models import Requirement
from .trames import Row


def rows_from_outline(entries):
    """Sommaire PDF (profondeur, titre) -> lignes de grille de type « section »."""
    rows, chapter = [], ""
    for depth, title in entries:
        match = re.match(r"^((?:[IVX]+|\d+|ANNEXE [A-Z])(?:\.\d+)*)\.?\s+(.*)$", title)
        ref, text = (match.group(1), match.group(2)) if match else ("", title)
        if depth == 1:
            chapter = title
        rows.append(Row("section", ref, chapter, text))
    return rows


def _unique_key(kind, ref, seen):
    ref = ref[:55]  # le champ fait 60 caractères, suffixe « #n » compris
    count = seen.get((kind, ref), 0)
    seen[(kind, ref)] = count + 1
    return (kind, ref if not count else f"{ref}#{count + 1}")


@transaction.atomic
def sync_requirements(referential, rows):
    """
    Remplace la grille du référentiel par `rows` (objets Row, avec `checks`).

    Les lignes sont rapprochées par (type, référence) : une ligne qui existe
    déjà est mise à jour, ce qui conserve la progression des utilisateurs ;
    seules les lignes disparues du référentiel sont supprimées.
    """
    existing = {(r.kind, r.ref): r for r in referential.requirements.all()}
    kept, seen, position = set(), {}, 0

    def save(row, parent=None):
        nonlocal position
        position += 1
        key = _unique_key(row.kind, row.ref or f"#{position}", seen)
        values = {
            "parent": parent, "theme": row.theme[:255], "text": row.text,
            "guidance": row.guidance, "evidence": row.evidence,
            "weight": row.weight, "position": position,
        }
        requirement = existing.get(key)
        if requirement is None:
            requirement = Requirement.objects.create(
                referential=referential, kind=key[0], ref=key[1], **values)
        else:
            for name, value in values.items():
                setattr(requirement, name, value)
            requirement.save()
        kept.add(requirement.pk)
        return requirement

    for row in rows:
        parent = save(row)
        for check in row.checks:
            save(check, parent)

    referential.requirements.exclude(pk__in=kept).delete()
    return len(kept)
