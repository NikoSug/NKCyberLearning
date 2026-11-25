"""
Recherche globale : guides (fiches et texte complet), cours, podcasts, lexique, référentiels et
certifications, dans une même page, avec un onglet par type de contenu.

Chaque mot cherché doit apparaître (sans tenir compte des accents ni des majuscules) ; les résultats
sont ensuite classés par pertinence : mots présents dans le titre d'abord, puis dans le résumé,
puis ailleurs. Le texte des guides donne un extrait centré sur le passage trouvé.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from django.db.models import Q

from courses.models import Course, Lesson
from podcasts.models import Episode, Series

from .models import Glossary, GlossaryEntry, GuideSection

MAX_TERMS = 8
SNIPPET_CHARS = 240
TAB_LIMIT = 60      # résultats par onglet
PREVIEW = 5         # résultats par type dans l'onglet « Tout »


def fold(text):
    """Minuscules sans accents (« Réseau » → « reseau »), pour comparer comme la base (unaccent)."""
    return (
        unicodedata.normalize("NFKD", (text or "").lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def terms(query):
    return [t for t in (query or "").split() if len(t) > 1][:MAX_TERMS]


def matching(queryset, words, fields):
    """Chaque mot doit apparaître dans au moins un des champs."""
    for word in words:
        condition = Q()
        for name in fields:
            condition |= Q(**{f"{name}__sans_accent__icontains": word})
        queryset = queryset.filter(condition)
    return queryset


def score(words, title="", summary="", other=""):
    """Pertinence : titre (3 points par mot, +5 si tous y sont), résumé (2), reste (1)."""
    title, summary, other = fold(title), fold(summary), fold(other)
    folded = [fold(w) for w in words]
    points = sum(3 if w in title else 2 if w in summary else 1 if w in other else 0 for w in folded)
    return points + (5 if folded and all(w in title for w in folded) else 0)


def snippet(text, words, size=SNIPPET_CHARS):
    """Extrait du texte autour de la première occurrence d'un des mots (même longueur une fois
    replié)."""
    folded = fold(text)
    positions = [folded.find(fold(w)) for w in words if fold(w) in folded]
    if not positions:
        return text[:size].strip() + ("…" if len(text) > size else "")
    start = max(0, min(positions) - size // 3)
    start = text.rfind(" ", 0, start) + 1 if start else 0
    end = min(len(text), start + size)
    end = text.find(" ", end) if text.find(" ", end) != -1 else len(text)
    return (
        ("…" if start else "")
        + re.sub(r"\s+", " ", text[start:end]).strip()
        + ("…" if end < len(text) else "")
    )


@dataclass
class Hit:
    """Résultat homogène pour les types sans gabarit dédié (cours, podcasts, passages des
    guides)."""
    title: str
    url: str
    kind: str
    meta: list = field(default_factory=list)
    excerpt: str = ""
    score: int = 0
    obj: object = None


def guide_passages(guides, words):
    """Passages du texte complet des guides (une ligne par guide : son meilleur chapitre)."""
    best = {}
    sections = matching(GuideSection.objects.filter(guide__in=guides), words, ["text", "title"]) \
        .select_related("guide").only("guide__title", "guide__slug", "title", "text", "position")
    for section in sections[:400]:
        points = score(words, section.title, "", section.text) + sum(
            fold(section.text).count(fold(w)) for w in words
        )
        if section.guide_id not in best or points > best[section.guide_id].score:
            best[section.guide_id] = Hit(
                title=section.guide.title,
                url=section.guide.get_absolute_url(),
                kind="texte",
                meta=[f"Chapitre : {section.title}"],
                excerpt=snippet(section.text, words),
                score=points,
                obj=section.guide,
            )
    return sorted(best.values(), key=lambda h: -h.score)


def courses(words, category="", level=""):
    published = Course.objects.filter(status=Course.Status.PUBLISHED).select_related(
        "category", "guide"
    )
    if category:
        published = published.filter(Q(category__slug=category) | Q(guide__category__slug=category))
    if level:
        published = published.filter(level=level)
    in_lessons = matching(
        Lesson.objects.filter(course__in=published), words, ["title", "body", "objective"]
    )
    lesson_hits = {}
    for lesson in in_lessons.only("course_id", "title", "body", "position"):
        lesson_hits.setdefault(lesson.course_id, lesson)
    found = matching(published, words, ["title", "summary"]) | published.filter(pk__in=lesson_hits)
    hits = []
    for course in found.distinct():
        lesson = lesson_hits.get(course.pk)
        meta = [course.get_kind_display(), course.get_level_display()]
        if lesson:
            meta.append(f"Leçon {lesson.position} : {lesson.title}")
        hits.append(
            Hit(
                title=course.title,
                url=course.get_absolute_url(),
                kind="cours",
                meta=meta,
                excerpt=snippet(lesson.body, words) if lesson else course.summary,
                score=score(words, course.title, course.summary, lesson.body if lesson else ""),
            )
        )
    return sorted(hits, key=lambda h: -h.score)


def podcasts(words, category="", level=""):
    published = Series.objects.filter(status=Series.Status.PUBLISHED).select_related("category")
    if category:
        published = published.filter(category__slug=category)
    if level:
        published = published.filter(level=level)
    episodes = {}
    for episode in matching(
        Episode.objects.filter(series__in=published), words, ["title", "summary"]
    ):
        episodes.setdefault(episode.series_id, episode)
    found = matching(published, words, ["title", "summary", "category__name"]) | published.filter(
        pk__in=episodes
    )
    hits = []
    for series in found.distinct():
        episode = episodes.get(series.pk)
        meta = [series.category.name, series.duration_display]
        if episode:
            meta.append(f"Épisode {episode.position} : {episode.title}")
        hits.append(
            Hit(
                title=series.title,
                url=series.get_absolute_url(),
                kind="podcast",
                meta=meta,
                excerpt=episode.summary if episode else series.summary,
                score=score(words, series.title, series.summary, episode.title if episode else ""),
            )
        )
    return sorted(hits, key=lambda h: -h.score)


def glossary_entries(guides, words):
    """Sigles et termes : le terme exact d'abord, une entrée par terme (avec les guides qui
    l'emploient)."""
    entries = matching(GlossaryEntry.objects.filter(glossary__status=Glossary.Status.PUBLISHED,
                                                    glossary__guide__in=guides),
                       words, ["term", "expansion", "definition"]).select_related("glossary__guide")
    grouped = {}
    for entry in entries.order_by("key", "glossary__published_at")[:300]:
        item = grouped.setdefault(
            entry.key,
            {
                "entry": entry,
                "guides": [],
                "score": score(words, entry.term, entry.expansion, entry.definition),
            },
        )
        if entry.glossary.guide not in item["guides"]:
            item["guides"].append(entry.glossary.guide)
    query = fold(" ".join(words))
    return sorted(
        grouped.values(),
        key=lambda i: (fold(i["entry"].term) != query, -i["score"], i["entry"].key),
    )
