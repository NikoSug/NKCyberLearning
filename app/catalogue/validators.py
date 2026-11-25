from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError


def _validate(uploaded_file, suffix, signature, kind):
    """Extension, taille et signature binaire : le nom ou le type MIME envoyé par le navigateur ne
    sont pas fiables."""
    if Path(uploaded_file.name).suffix.lower() != suffix:
        raise ValidationError(f"Seuls les fichiers {suffix} sont acceptés.")

    max_bytes = settings.GUIDE_MAX_UPLOAD_MB * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise ValidationError(f"Le fichier dépasse {settings.GUIDE_MAX_UPLOAD_MB} Mo.")

    position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else 0
    uploaded_file.seek(0)
    header = uploaded_file.read(len(signature))
    uploaded_file.seek(position)
    if header != signature:
        raise ValidationError(f"Le contenu du fichier n'est pas un {kind} valide.")


def validate_pdf(uploaded_file):
    """Refuse tout fichier qui n'est pas un vrai PDF (signature %PDF-)."""
    _validate(uploaded_file, ".pdf", b"%PDF-", "PDF")


def validate_xlsx(uploaded_file):
    """Refuse tout fichier qui n'est pas un classeur Excel .xlsx (archive ZIP)."""
    _validate(uploaded_file, ".xlsx", b"PK\x03\x04", "classeur Excel")
