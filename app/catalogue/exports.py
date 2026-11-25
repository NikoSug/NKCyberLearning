"""Export Excel d'une grille d'exigences, avec la progression de l'utilisateur."""
import io

from .models import Requirement, RequirementProgress

HEADERS = ["Thème", "Référence", "Type", "Exigence / domaine", "Pondération", "Précisions",
           "Points de vérification", "Preuves attendues", "Mon statut"]
WIDTHS = [30, 16, 16, 70, 12, 40, 70, 40, 16]


def _safe(value):
    """Une cellule qui commence par = + - @ serait interprétée comme une formule par le tableur."""
    value = value or ""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


def grid_workbook(referential, statuses):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (referential.short_name or "Grille")[:31]
    version = f" — version {referential.version}" if referential.version else ""
    sheet.append([f"{referential.name}{version}"])
    sheet.append(
        [f"Origine de la grille : {referential.grid_source}" if referential.grid_source else ""]
    )
    sheet.append([])
    sheet.append(HEADERS)
    header_row = sheet.max_row
    for cell in sheet[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4FD1")
    sheet["A1"].font = Font(bold=True, size=13)

    labels = dict(RequirementProgress.Status.choices)
    rows = list(referential.requirements.all())
    checks = {}
    for row in rows:
        if row.parent_id:
            checks.setdefault(row.parent_id, []).append(row)

    for row in rows:
        if row.parent_id:
            continue
        verifications = "\n".join(
            f"{c.ref} : {c.text}" if c.ref else c.text for c in checks.get(row.pk, []))
        evidence = "\n".join(
            filter(None, [row.evidence] + [c.evidence for c in checks.get(row.pk, [])])
        )
        status = statuses.get(row.pk, RequirementProgress.Status.TODO) \
            if row.kind in Requirement.TRACKED_KINDS else ""
        sheet.append([_safe(v) for v in (
            row.theme, row.ref, row.get_kind_display(), row.text, row.weight, row.guidance,
            verifications, evidence, labels.get(status, ""))])

    for index, width in enumerate(WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for line in sheet.iter_rows(min_row=header_row + 1):
        for cell in line:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(HEADERS))}{sheet.max_row}"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
