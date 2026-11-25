"""Mise en évidence des mots cherchés dans les résultats de recherche (sans tenir compte des
accents)."""

import unicodedata

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


def _fold_char(char):
    return unicodedata.normalize("NFKD", char.lower()).encode("ascii", "ignore").decode("ascii")


@register.filter
def highlight(text, words):
    """Entoure de <mark> chaque occurrence des mots ; le reste du texte est échappé."""
    text = str(text or "")
    if not words:
        return escape(text)
    # Texte replié caractère par caractère, avec la position d'origine de chaque caractère replié.
    folded, origin = [], []
    for index, char in enumerate(text):
        for piece in _fold_char(char) or " ":
            folded.append(piece)
            origin.append(index)
    folded = "".join(folded)
    marked = [False] * len(text)
    for word in words:
        target = "".join(_fold_char(c) for c in word)
        start = folded.find(target) if target else -1
        while start != -1:
            for position in range(start, start + len(target)):
                marked[origin[position]] = True
            start = folded.find(target, start + len(target))
    html, opened = [], False
    for char, is_marked in zip(text, marked):
        if is_marked and not opened:
            html.append("<mark>")
        elif not is_marked and opened:
            html.append("</mark>")
        opened = is_marked
        html.append(escape(char))
    if opened:
        html.append("</mark>")
    return mark_safe("".join(html))  # nosec B308 B703 (chaque caractère est échappé ci-dessus)
