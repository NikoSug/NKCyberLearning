"""Affichage du texte des leçons : format simple (« ## Titre », listes « - … » ou « 1. … »,
**gras**), HTML échappé."""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()
BULLET_RE = re.compile(r"^[-*•]\s+")
NUMBERED_RE = re.compile(r"^\d+[.)]\s+")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")


def _inline(text):
    return ITALIC_RE.sub(r"<em>\1</em>", BOLD_RE.sub(r"<strong>\1</strong>", escape(text)))


def _mark_terms(html, entries):
    """
    Souligne la première apparition de chaque terme du lexique (hors titres) ; un clic ouvre sa
    définition. Passage par des marqueurs : une définition n'est jamais réécrite par un autre terme.
    """
    markers, used = [], set()
    for entry in sorted(entries, key=lambda e: -len(e.term)):
        if entry.key in used:
            continue
        # Sigles : casse exacte (« SI » mais pas « si ») ; termes : indifférent à la casse («
        # Pare-feu », « pare-feu »).
        flags = 0 if entry.kind == "sigle" else re.IGNORECASE
        pattern = re.compile(rf"(?<![\w&;-]){re.escape(escape(entry.term))}(?![\w-])", flags)
        parts = re.split(
            r"(<h[23]>.*?</h[23]>|<[^>]+>)", html
        )  # ni dans les titres, ni dans les balises
        for index, part in enumerate(parts):
            if part.startswith("<"):
                continue
            match = pattern.search(part)
            if match:
                title = (
                    f"{entry.expansion} — {entry.definition}"
                    if entry.expansion
                    else entry.definition
                )
                markers.append(f'<abbr class="term" tabindex="0" data-term="{escape(entry.key)}" '
                               f'title="{escape(title)}">{match.group(0)}</abbr>')
                parts[index] = (
                    f"{part[:match.start()]}\x00{len(markers) - 1}\x00{part[match.end():]}"
                )
                used.add(entry.key)
                break
        html = "".join(parts)
    return re.sub(r"\x00(\d+)\x00", lambda m: markers[int(m.group(1))], html)


@register.filter
def lesson_html(text, entries=None):
    html, paragraph, items = [], [], []
    list_tag = ["ul"]

    def flush():
        if paragraph:
            html.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            tag = list_tag[0]
            html.append(f"<{tag}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>")
            items.clear()

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("#"):
            flush()
            level = 3 if stripped.startswith("###") else 2
            html.append(f"<h{level}>{_inline(stripped.lstrip('#').strip())}</h{level}>")
        elif BULLET_RE.match(stripped) or NUMBERED_RE.match(stripped):
            tag = "ul" if BULLET_RE.match(stripped) else "ol"
            if paragraph or (items and list_tag[0] != tag):
                flush()
            list_tag[0] = tag
            items.append(_inline((BULLET_RE if tag == "ul" else NUMBERED_RE).sub("", stripped)))
        else:
            if items:
                flush()
            paragraph.append(_inline(stripped))
    flush()
    result = "".join(html)
    if entries:
        result = _mark_terms(result, entries)
    return mark_safe(
        result
    )  # nosec B308 B703 (tout le texte est échappé par _inline et _mark_terms)
