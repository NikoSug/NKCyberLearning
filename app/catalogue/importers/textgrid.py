"""
Extraction des exigences depuis le texte des référentiels PDF de l'ANSSI
qui n'ont pas de trame d'évaluation Excel.

Deux mises en forme sont reconnues :
  - « lettrée » (PVID, PAMS, MIE, eIDAS…) : chapitres numérotés « IV.2.1. Titre »
    puis exigences « a) Le prestataire doit… » ;
  - ReCyF (NIS 2) : moyens de conformité « 7.A.5-EE » suivis du texte et de
    l'applicabilité aux entités importantes (EI) et essentielles (EE).
"""
import io
import re

from .trames import Row

NOISE = re.compile(
    r"critère de diffusion|^\s*\d+(\.\d+)?\s+\d{2}/\d{2}/\d{4}\s+\w+\s+\d+/\d+\s*$|"
    r"^\s*page \d+|^\s*\d+\s*$|^\s*\d+\s*/\s*\d+\s*$|^RECYF\s*:", re.I)


def pdf_text(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=False)
    lines = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        lines.extend(line.rstrip() for line in text.splitlines() if not NOISE.search(line))
    return lines


def _join(parts):
    text = " ".join(p.strip() for p in parts if p.strip())
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


SECTION = re.compile(r"^\s*((?:[IVX]+|\d+)(?:\.\d+)+)\.?\s+([A-ZÀ-Ý].{2,150})$")
ITEM = re.compile(r"^\s*([a-z]{1,2})\)\s+(.*)$")
NORMATIVE = re.compile(r"\bdoit\b|\bdoivent\b|recommand", re.I)


def parse_lettered(lines):
    """Exigences « a) … » rattachées au dernier chapitre numéroté rencontré."""
    rows, section_ref, section_title, current, buffer = [], "", "", None, []

    def flush():
        if current is not None:
            text = _join(buffer)
            if NORMATIVE.search(text) and len(text) > 25:
                current.text = text
                current.kind = (
                    "recommendation"
                    if text.lower().startswith("il est recommandé")
                    else "requirement"
                )
                rows.append(current)

    for line in lines:
        section = SECTION.match(line)
        if section and not line.strip().endswith((".", ";", ",")) and "...." not in line:
            flush()
            current, buffer = None, []
            section_ref, section_title = section.group(1), section.group(2).strip()
            continue
        item = ITEM.match(line)
        if item and section_ref:
            flush()
            current = Row("requirement", f"{section_ref}.{item.group(1)})",
                          f"{section_ref}. {section_title}", "")
            buffer = [item.group(2)]
            continue
        if current is not None:
            buffer.append(line)
    flush()

    # Les tables des matières produisent des doublons : on garde la dernière occurrence.
    unique = {}
    for row in rows:
        unique[row.ref] = row
    return list(unique.values())


RECYF_ID = re.compile(r"^\s*(\d+(?:\.[A-Z])?\.\d+)-(EI/EE|EI|EE)\s*$")
RECYF_OBJECTIVE_TOC = re.compile(r"^Objectif de sécurité (\d+)\.\s+(.+?)[\s.]*\d*\s*$")
RECYF_OBJECTIVE = re.compile(r"^OBJECTIF DE SÉCURITÉ (\d+)\.")
RECYF_APPLICABILITY = re.compile(r"^(.*?)\s*(Oui|Non)\s+(Oui|Non)\s*$")


def parse_recyf(lines):
    titles, rows = {}, []
    for index, line in enumerate(lines):
        toc = RECYF_OBJECTIVE_TOC.match(line.strip())
        if toc and toc.group(1) not in titles:
            title = toc.group(2)
            # Titre sur deux lignes dans le sommaire : la première ne se termine pas par le n° de
            # page.
            if (
                not re.search(r"\d\s*$", line)
                and index + 1 < len(lines)
                and not lines[index + 1].strip().lower().startswith("objectif")
            ):
                title = f"{title} {lines[index + 1]}"
            titles[toc.group(1)] = re.sub(r"[\s.]*\d+\s*$", "", title).strip(" .")

    objective, current, buffer = "", None, []

    def flush():
        if current is None:
            return
        text = _join(buffer)
        match = RECYF_APPLICABILITY.match(text)
        if match:
            text = match.group(1)
            current.guidance = (f"Attendu d'une entité importante : {match.group(2)} · "
                                f"d'une entité essentielle : {match.group(3)}")
        current.text = text.strip()
        if current.text:
            rows.append(current)

    for line in lines:
        heading = RECYF_OBJECTIVE.match(line.strip())
        if heading:
            flush()
            current, buffer = None, []
            objective = heading.group(1)
            continue
        found = RECYF_ID.match(line)
        if found:
            flush()
            ref = f"{found.group(1)}-{found.group(2)}"
            theme = f"Objectif {objective}. {titles.get(objective, '')}".strip(" .")
            current, buffer = Row("requirement", ref, theme, ""), []
            continue
        if current is not None:
            if line.isupper() and len(line.strip()) > 4:
                flush()  # intertitre en capitales : fin de l'exigence en cours
                current, buffer = None, []
                continue
            buffer.append(line)
            if RECYF_APPLICABILITY.match(line.strip()):
                flush()
                current, buffer = None, []
    flush()
    return rows
