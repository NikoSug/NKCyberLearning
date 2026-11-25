"""
Vue commune des contenus de chaque type pour l'accueil et les listes de l'atelier : brouillons,
contributions à relire et contenus publiés, avec ce que chacun a le droit de voir.

  - un rédacteur voit ses propres contributions ;
  - un gestionnaire de contenu voit tout (y compris ce qui a été importé ou généré par l'IA, une
  fois publié).
"""

from dataclasses import dataclass, field

from django.db.models import Q
from django.urls import reverse

from catalogue.models import (
    AudioUpload,
    Certification,
    Glossary,
    Guide,
    GuideCategory,
    QuizDraft,
    ReadingPath,
    Referential,
)
from courses.models import Course
from podcasts.models import Series

BUCKETS = [("brouillons", "Brouillons"), ("relecture", "À relire"), ("publies", "Publiés")]
STATUS_LABELS = {"brouillons": "En rédaction", "relecture": "À relire", "publies": "Publié"}


@dataclass
class Entry:
    title: str
    url: str
    bucket: str
    meta: list = field(default_factory=list)
    author: object = None
    when: object = None
    status: str = ""  # précision (« l'IA écrit le premier jet », « à enregistrer »…)


def bucket_for(status):
    """Onglet de liste d'un contenu selon son état (« redaction », « a_relire », « publie », « »
    …)."""
    if status in ("a_relire", "a_enregistrer", "enregistrement"):
        return "relecture"
    if status in ("publie", ""):
        return "publies"
    return "brouillons"


def list_url(key, status):
    return reverse("studio:list", args=[key]) + f"?etat={bucket_for(status)}"


def _search(queryset, query, *fields):
    if not query:
        return queryset
    condition = Q()
    for name in fields:
        condition |= Q(**{f"{name}__icontains": query})
    return queryset.filter(condition)


def _scope(queryset, user):
    return queryset if user.is_content_manager else queryset.filter(author=user)


# --- Contenus du catalogue (champ editorial_status)
# ---------------------------------------------------
def _editorial(key, queryset, user, bucket, query, title_fields, label, meta):
    if bucket == "brouillons":
        queryset = _scope(queryset.filter(editorial_status="redaction"), user)
    elif bucket == "relecture":
        queryset = _scope(queryset.filter(editorial_status="a_relire"), user)
    else:
        queryset = queryset.filter(is_published=True, editorial_status="")
        if not user.is_content_manager:
            queryset = queryset.filter(author=user)
    queryset = _search(queryset, query, *title_fields)
    return [
        Entry(
            label(item),
            reverse("studio:edit", args=[key, item.pk]),
            bucket,
            meta(item),
            item.author,
            getattr(item, "updated_at", None) or getattr(item, "submitted_at", None),
        )
        for item in queryset.select_related("author")[:200]
    ]


def _level(item):
    return item.get_level_display() if getattr(item, "level", "") else ""


def guides(user, bucket, query=""):
    return _editorial(
        "guide",
        Guide.objects.select_related("category").order_by("-updated_at"),
        user,
        bucket,
        query,
        ["title"],
        lambda g: g.title,
        lambda g: [g.category.name if g.category else "Sans domaine", _level(g)],
    )


def referentials(user, bucket, query=""):
    queryset = Referential.objects.exclude(kind=Referential.Kind.EXAM).order_by("-updated_at")
    return _editorial(
        "referentiel",
        queryset,
        user,
        bucket,
        query,
        ["name", "short_name"],
        lambda r: r.title,
        lambda r: [r.get_kind_display(), r.version_display if r.version else ""],
    )


def programs(user, bucket, query=""):
    queryset = Referential.objects.filter(kind=Referential.Kind.EXAM).order_by("-updated_at")
    return _editorial(
        "programme",
        queryset,
        user,
        bucket,
        query,
        ["name", "short_name"],
        lambda r: r.title,
        lambda r: [r.issuer],
    )


def certifications(user, bucket, query=""):
    return _editorial(
        "certification",
        Certification.objects.order_by("-updated_at"),
        user,
        bucket,
        query,
        ["name", "acronym"],
        lambda c: f"{c.name} ({c.acronym})" if c.acronym else c.name,
        lambda c: [c.get_kind_display(), c.issuer],
    )


def paths(user, bucket, query=""):
    return _editorial(
        "parcours",
        ReadingPath.objects.order_by("-updated_at"),
        user,
        bucket,
        query,
        ["title"],
        lambda p: p.title,
        lambda p: [_level(p), f"{p.steps.count()} guide(s)"],
    )


def audios(user, bucket, query=""):
    queryset = AudioUpload.objects.select_related("guide").order_by("-created_at")
    if bucket == "publies":
        queryset = _scope(queryset.filter(editorial_status="", published_at__isnull=False), user)
    else:
        queryset = _scope(
            queryset.filter(editorial_status="redaction" if bucket == "brouillons" else "a_relire"),
            user,
        )
    queryset = _search(queryset, query, "guide__title")
    return [
        Entry(
            a.guide.title,
            reverse("studio:audio_edit", args=[a.pk]),
            bucket,
            [f"{a.parts.count()} chapitre(s)"],
            a.author,
            a.created_at,
        )
        for a in queryset[:200]
    ]


def domains(user, bucket, query=""):
    if bucket != "publies" or not user.is_content_manager:
        return []
    queryset = _search(GuideCategory.objects.all(), query, "name")
    return [
        Entry(
            c.name,
            reverse("studio:edit", args=["domaine", c.pk]),
            bucket,
            [f"{c.guides.count()} guide(s)"],
        )
        for c in queryset
    ]


# --- Contenus d'apprentissage (champ status)
# --------------------------------------------------------
def _learning(
    queryset, user, bucket, query, writing, review, published, url, label, meta, extra_scope=None
):
    statuses = {"brouillons": writing, "relecture": review, "publies": published}[bucket]
    queryset = queryset.filter(status__in=statuses)
    if user.is_content_manager:
        if bucket != "publies":
            queryset = queryset.filter(
                author__isnull=False
            )  # les brouillons de l'IA se relisent dans la Relecture
    else:
        queryset = queryset.filter(author=user)
    queryset = _search(queryset, query, *(extra_scope or ["title"]))
    return [
        Entry(
            label(item),
            url(item, bucket),
            bucket,
            meta(item),
            item.author,
            getattr(item, "generated_at", None) or item.requested_at,
            (
                item.get_status_display()
                if item.status not in ("redaction", "a_relire", "publie")
                else ""
            ),
        )
        for item in queryset.select_related("author")[:200]
    ]


def courses(user, bucket, query=""):
    return _learning(
        Course.objects.select_related("category", "guide").order_by("-requested_at"),
        user,
        bucket,
        query,
        ["redaction", "attente", "generation", "echec"],
        ["a_relire"],
        ["publie"],
        lambda c, b: reverse("courses:review_course", args=[c.pk]),
        lambda c: c.title,
        lambda c: [c.source_title, c.get_level_display()],
    )


def podcasts(user, bucket, query=""):
    return _learning(
        Series.objects.select_related("category").order_by("-requested_at"),
        user,
        bucket,
        query,
        ["redaction", "attente", "generation", "echec"],
        ["a_relire", "a_enregistrer", "enregistrement"],
        ["publie"],
        lambda s, b: reverse("podcasts:review_series", args=[s.pk]),
        lambda s: s.title,
        lambda s: [s.category.name],
    )


def quizzes(user, bucket, query=""):
    return _learning(
        QuizDraft.objects.select_related("guide").order_by("-requested_at"),
        user,
        bucket,
        query,
        ["redaction"],
        ["a_relire"],
        ["publie"],
        lambda d, b: reverse("studio:qcm_edit", args=[d.pk]),
        lambda d: d.guide.title,
        lambda d: [f"{d.questions.count()} question(s)"],
        ["guide__title"],
    )


def glossaries(user, bucket, query=""):
    return _learning(
        Glossary.objects.select_related("guide").order_by("-requested_at"),
        user,
        bucket,
        query,
        ["redaction", "attente", "generation", "echec"],
        ["a_relire"],
        ["publie"],
        lambda g, b: reverse("catalogue:glossary_review", args=[g.pk]),
        lambda g: g.guide.title,
        lambda g: [f"{g.entries.count()} entrée(s)"],
        ["guide__title"],
    )


LISTERS = {
    "guide": guides,
    "domaine": domains,
    "referentiel": referentials,
    "programme": programs,
    "certification": certifications,
    "parcours": paths,
    "cours": courses,
    "podcast": podcasts,
    "qcm": quizzes,
    "lexique": glossaries,
    "audio": audios,
}


def entries(key, user, bucket, query=""):
    return LISTERS[key](user, bucket, query)


def counts(key, user):
    return {bucket: len(entries(key, user, bucket)) for bucket, _label in BUCKETS
            if bucket != "publies"}
