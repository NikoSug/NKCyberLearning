"""
Lecture des trames d'évaluation de l'ANSSI (fichiers Excel PASSI, PRIS, PDIS,
PACS, SecNumCloud…).

Les trames n'ont pas toutes la même mise en page : les colonnes sont donc
repérées par leur intitulé (« Exigence(s) détaillée(s) », « Vérification(s) »,
« Précision(s) »…), et non par leur position.
"""
import io
import re
from dataclasses import dataclass, field

HEADER_PATTERNS = {
    "theme": r"^(thème|theme|chapitres? du référentiel)",
    "ref": r"^(ir ref|références? (des )?exigences|référence)",
    "type": r"^type\s*$",
    "requirement": r"^exigence\(s\) détaillée",
    "verification": r"^vérification",
    "guidance": r"^précision",
    "evidence": r"exemple\(s\) de preuve",
    "chapter": r"^schap$",
}


@dataclass
class Row:
    kind: str  # requirement | recommendation | verification | section | domain
    ref: str
    theme: str
    text: str
    guidance: str = ""
    evidence: str = ""
    weight: str = ""
    checks: list = field(default_factory=list)


def _clean(value):
    if value is None:
        return ""
    text = str(value).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _find_columns(row):
    columns = {}
    for index, cell in enumerate(row):
        label = _clean(cell).lower()
        for key, pattern in HEADER_PATTERNS.items():
            if key not in columns and re.search(pattern, label):
                columns[key] = index
    return columns


# Onglets annexes (II 901, guide d'hygiène…) : ils reprennent d'autres référentiels.
ANNEX_SHEETS = re.compile(r"ii[- ]?901|ghy|ghi|non-conformit|soutenance|examen", re.I)


def _requirement_sheet(workbook):
    """La feuille principale est celle (hors annexes) qui contient le plus d'exigences."""
    best, best_count = None, 0
    for sheet in workbook.worksheets:
        if ANNEX_SHEETS.search(sheet.title):
            continue
        rows = list(sheet.iter_rows(values_only=True))
        for position, row in enumerate(rows[:10]):
            columns = _find_columns(row)
            if "requirement" in columns and "ref" in columns:
                count = sum(1 for r in rows[position + 1:]
                            if len(r) > columns["requirement"] and r[columns["requirement"]])
                if count > best_count:
                    best, best_count = (rows, position, columns), count
                break
    return best


def parse_trame(data):
    """Renvoie la liste des exigences (avec leurs points de vérification) d'une trame."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    found = _requirement_sheet(workbook)
    if not found:
        return []
    rows, header_index, columns = found

    def cell(row, key):
        index = columns.get(key)
        return _clean(row[index]) if index is not None and index < len(row) else ""

    results, current, theme = [], None, ""
    for row in rows[header_index + 1:]:
        if not any(row):
            continue
        ref, text = cell(row, "ref"), cell(row, "requirement")
        verification = cell(row, "verification")
        row_theme = cell(row, "theme") or (
            f"Chapitre {cell(row, 'chapter')}" if cell(row, "chapter") else ""
        )
        if not ref and not text and not verification:
            # Ligne de titre de chapitre (texte seul dans la première colonne).
            first = _clean(row[0])
            if first and len(first) < 200:
                theme = re.sub(r"\s+", " ", first)
            continue
        theme = re.sub(r"\s+", " ", row_theme) or theme
        kind = cell(row, "type").lower()

        if kind.startswith("vérif") and current is not None:
            current.checks.append(Row("verification", ref, theme, verification,
                                      cell(row, "guidance"), cell(row, "evidence")))
            continue
        if not text:
            continue
        current = Row(
            "recommendation" if kind.startswith("recommandation") else "requirement",
            ref, theme, text, cell(row, "guidance"), cell(row, "evidence"))
        # Trames sans colonne « Type » (PDIS) : la vérification est sur la même ligne.
        if verification and "type" not in columns:
            current.checks.append(Row("verification", ref, theme, verification))
        results.append(current)
    return results
