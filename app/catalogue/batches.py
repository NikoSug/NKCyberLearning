"""
Mise en file par lots, identique pour chaque génération (QCM, cours, podcasts, lexiques, audio) :
« les 10 prochains » ou « tout ». Les guides sont pris du niveau débutant vers l'avancé ;
pour les cours, les thèmes passent d'abord (le point d'entrée des débutants), puis les guides.
"""
from courses import coursegen
from podcasts import podcastgen

from . import generation, glossary, narration, quizgen
from .models import Guide

BATCH = 10
KINDS = {
    # type : (unité au pluriel, unité au singulier)
    "qcm": ("QCM", "QCM"),
    "cours": ("cours", "cours"),
    "podcast": ("séries de podcasts", "série de podcasts"),
    "lexique": ("lexiques", "lexique"),
    "audio": ("versions audio", "version audio"),
}


def _by_level(guides):
    return list(guides.order_by("level", "title"))


def candidates(kind):
    """Ce qui manque encore, dans l'ordre où ce sera mis en file."""
    if kind == "qcm":
        return _by_level(quizgen.guides_without_quiz())
    if kind == "cours":
        return list(coursegen.themes_without_course().order_by("position", "name")) \
            + _by_level(coursegen.guides_without_course())
    if kind == "podcast":
        return list(podcastgen.themes_without_series().order_by("position", "name"))
    if kind == "lexique":
        return _by_level(glossary.guides_without_glossary())
    # Audio : les guides sans version audio d'abord, puis ceux dont la version date d'un système
    # précédent.
    eligible = generation.eligible_guides()
    return _by_level(eligible.filter(narration__isnull=True)) + _by_level(
        narration.outdated_guides(eligible)
    )


def missing(kind):
    return len(candidates(kind))


def queue(kind, limit=None, user=None):
    """Met en file les `limit` prochains (tous si None) ; renvoie le nombre mis en file."""
    items = candidates(kind)[:limit] if limit else candidates(kind)
    for item in items:
        if kind == "qcm":
            quizgen.request(item, user)
        elif kind == "cours":
            (coursegen.request_guide if isinstance(item, Guide) else coursegen.request_theme)(
                item, user
            )
        elif kind == "podcast":
            podcastgen.request_theme(item, user)
        elif kind == "lexique":
            glossary.request(item, user)
        else:
            narration.request(item, user, priority=narration.BULK_PRIORITY)
    return len(items)
