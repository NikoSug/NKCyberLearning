"""
Grille d'exigences d'un référentiel, ou domaines d'un programme d'examen, écrite en texte simple :

    ## Chapitre ou partie                    → section (titre de regroupement)
    - R12 : Libellé de l'exigence            → exigence (ou domaine pour un programme d'examen)
    - Domaine 1 : Sécurité et risques [15 %] → pondération entre crochets, en fin de ligne
      - Point de vérification                → sous-ligne de l'exigence précédente
      > Précision                            → précision de la ligne précédente

L'enregistrement passe par `sync_requirements` : une ligne qui garde sa référence garde aussi la
progression des apprenants.
"""

import re

from catalogue.importers.trames import Row
from catalogue.models import Requirement

Kind = Requirement.Kind
REF_RE = re.compile(r"^([A-Za-z]{0,6}[\d][\w.\-]{0,15}|[A-Z]{1,6}\.\d[\w.\-]{0,12})\s*:\s+(.+)$")
WEIGHT_RE = re.compile(r"\s*\[([^\]]{1,30})\]\s*$")


def _split(text):
    """« R12 : Libellé [10 %] » → (référence, libellé, pondération)."""
    weight = ""
    match = WEIGHT_RE.search(text)
    if match:
        weight, text = match.group(1).strip(), text[:match.start()]
    match = REF_RE.match(text.strip())
    if match:
        return match.group(1), match.group(2).strip(), weight
    return "", text.strip(), weight


def _ref(item):
    return "" if not item.ref or item.ref.startswith("#") else f"{item.ref.split('#')[0]} : "


def to_text(referential):
    lines = []
    tops = (
        referential.requirements.filter(parent__isnull=True)
        .prefetch_related("checks")
        .order_by("position")
    )
    for item in tops:
        weight = f" [{item.weight}]" if item.weight else ""
        if item.kind == Kind.SECTION:
            lines.append(f"\n## {_ref(item)}{item.text}{weight}")
        else:
            lines.append(f"- {_ref(item)}{item.text}{weight}")
        if item.guidance:
            lines.append(f"  > {item.guidance}")
        for check in item.checks.order_by("position"):
            lines.append(f"  - {_ref(check)}{check.text}")
            if check.guidance:
                lines.append(f"    > {check.guidance}")
    return "\n".join(lines).strip()


def parse(text, default_kind=Kind.REQUIREMENT):
    """Texte → lignes de grille (Row, avec leurs sous-lignes dans `checks`)."""
    rows, chapter, last = [], "", None
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        indented = raw.startswith(("  ", "\t"))
        line = raw.strip()
        if line.startswith(">"):
            if last is not None:
                last.guidance = (f"{last.guidance} " if last.guidance else "") + line[1:].strip()
            continue
        if line.startswith("##"):
            ref, label, weight = _split(line.lstrip("#").strip())
            chapter = label
            last = Row(Kind.SECTION, ref, chapter, label, weight=weight)
            rows.append(last)
            continue
        label_text = line[1:].strip() if line.startswith(("-", "*", "•")) else line
        ref, label, weight = _split(label_text)
        if not label:
            continue
        if indented and rows and rows[-1].kind != Kind.SECTION:
            last = Row(Kind.VERIFICATION, ref, chapter, label, weight=weight)
            rows[-1].checks.append(last)
        else:
            last = Row(default_kind, ref, chapter, label, weight=weight)
            rows.append(last)
    return rows


def count(rows):
    return sum(1 + len(row.checks) for row in rows)
