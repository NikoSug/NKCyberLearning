"""Schéma d'exemple d'une question de QCM, dessiné en SVG (voir catalogue/diagrams.py)."""
from django import template
from django.utils.safestring import mark_safe

from catalogue import diagrams

register = template.Library()


@register.filter
def diagram(text):
    """SVG du schéma ; rien si le schéma est vide ou invalide (les libellés sont échappés par le
    rendu)."""
    return (
        mark_safe(diagrams.render(text)) if text else ""
    )  # nosec B308 B703 (SVG construit par diagrams.render)
