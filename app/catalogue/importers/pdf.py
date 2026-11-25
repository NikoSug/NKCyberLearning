"""Lecture des PDF : nombre de pages et sommaire (signets), sans jamais exécuter leur contenu."""
import io
import logging
import re

logging.getLogger("pypdf").setLevel(logging.ERROR)  # PDF imparfaits : avertissements sans intérêt


def _reader(data):
    from pypdf import PdfReader
    return PdfReader(io.BytesIO(data), strict=False)


def page_count(data):
    try:
        return len(_reader(data).pages)
    except Exception:
        return None


def outline(data, max_depth=2):
    """
    Renvoie le sommaire du PDF sous forme de liste (profondeur, titre),
    limitée à `max_depth` niveaux. Liste vide si le PDF n'a pas de signets.
    """
    try:
        reader = _reader(data)
        root = reader.outline
    except Exception:
        return []

    entries = []

    def walk(items, depth):
        for item in items:
            if isinstance(item, list):
                if depth < max_depth:
                    walk(item, depth + 1)
                continue
            title = re.sub(r"\s+", " ", getattr(item, "title", "") or "").strip()
            if title:
                entries.append((depth, title))

    walk(root, 1)
    return entries
