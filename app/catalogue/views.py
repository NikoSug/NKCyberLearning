import json
import mimetypes
import re
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, Exists, IntegerField, Max, OuterRef, Q, Value, When
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from accounts.ratelimit import rate_limit
from progression import items, shop, vacations

from . import (
    assistant,
    calendar_export,
    generation,
    glossary,
    narration,
    narration_text,
    news,
    paths,
    quiz,
    quizgen,
    reading,
    revision,
    rewards,
)
from . import search as finder
from .exports import grid_workbook
from .forms import StudyPlanForm
from .models import (
    Certification,
    DailyReviewAttempt,
    Glossary,
    GlossaryEntry,
    Guide,
    GuideCategory,
    GuideCitation,
    GuideDocument,
    Language,
    Level,
    ListeningProgress,
    Narration,
    NarrationSection,
    NewsItem,
    QuizAttempt,
    QuizQuestion,
    ReadingListItem,
    ReadingPath,
    Referential,
    Requirement,
    RequirementProgress,
    StudyPlan,
    subject_filter,
)
from .planning import build_schedule

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SEARCH_FIELDS = {
    "guides": ["title", "summary", "theme", "collection", "category__name"],
    "certifications": ["name", "acronym", "description", "issuer", "domain"],
    "referentials": ["name", "short_name", "description"],
}


# Ordre d'affichage des certifications : prestataires, produits, puis les autres types.
CERTIFICATION_KIND_ORDER = [
    Certification.Kind.PROVIDER_QUALIFICATION,
    Certification.Kind.PRODUCT_QUALIFICATION,
    Certification.Kind.INDIVIDUAL,
    Certification.Kind.OTHER,
    Certification.Kind.PRODUCT_CERTIFICATION,
]


def _ordered_certifications(queryset):
    rank = Case(
        *[When(kind=kind, then=Value(i)) for i, kind in enumerate(CERTIFICATION_KIND_ORDER)],
        default=Value(99),
        output_field=IntegerField(),
    )
    return queryset.annotate(kind_rank=rank).order_by("kind_rank", "level", "name")


QUIZ_FILTERS = {"avec": "Avec QCM", "sans": "Sans QCM"}
AUDIO_FILTERS = {"avec": "Avec version audio", "sans": "Sans version audio"}


def _catalogue_filters(request):
    """
    Filtres communs aux onglets du catalogue : recherche, domaine, niveau.
    Les filtres « qcm » et « audio » ne s'appliquent qu'aux guides mais restent conservés d'un
    onglet à l'autre.
    """
    category = request.GET.get("domaine", "")
    level = request.GET.get("niveau", "")
    with_quiz = request.GET.get("qcm", "")
    with_audio = request.GET.get("audio", "")
    return {
        "q": request.GET.get("q", "").strip()[:100],
        "domaine": category if GuideCategory.objects.filter(slug=category).exists() else "",
        "niveau": level if level in Level.values else "",
        "qcm": with_quiz if with_quiz in QUIZ_FILTERS else "",
        "audio": with_audio if with_audio in AUDIO_FILTERS else "",
    }


def _catalogue_context(request, filters, **extra):
    query = {k: v for k, v in filters.items() if v}
    return {
        "filters": filters,
        "filter_query": f"?{urlencode(query)}" if query else "",
        "has_filters": bool(query),
        "categories": GuideCategory.objects.all(),
        "levels": Level.choices,
        "quiz_filters": QUIZ_FILTERS.items(),
        "audio_filters": AUDIO_FILTERS.items(),
        **extra,
    }


def _filter_certifications(queryset, filters):
    """Le domaine d'une certification : ses domaines conseillés et ceux des guides que cite son
    référentiel."""
    if filters["q"]:
        queryset = _search(queryset, filters["q"], SEARCH_FIELDS["certifications"])
    if filters["domaine"]:
        queryset = queryset.filter(
            Q(guide_categories__slug=filters["domaine"])
            | Q(referentials__citations__guide__category__slug=filters["domaine"])).distinct()
    if filters["niveau"]:
        queryset = queryset.filter(level=filters["niveau"])
    return queryset


def _filter_referentials(queryset, filters):
    """Domaine : guides cités ou domaines de la certification ; niveau : celui de la
    certification."""
    if filters["q"]:
        queryset = _search(queryset, filters["q"], SEARCH_FIELDS["referentials"])
    if filters["domaine"]:
        queryset = queryset.filter(
            Q(citations__guide__category__slug=filters["domaine"])
            | Q(certifications__guide_categories__slug=filters["domaine"])).distinct()
    if filters["niveau"]:
        queryset = queryset.filter(certifications__level=filters["niveau"]).distinct()
    return queryset


def _percent(part, total):
    return round(100 * part / total) if total else 0


def _guides_for(user):
    """Guides publiés, avec l'état de lecture de l'utilisateur, le nombre de séries de QCM et la
    version audio."""
    guides = (
        Guide.objects.filter(is_published=True)
        .select_related("category")
        .annotate(
            has_audio=Exists(
                Narration.objects.filter(guide=OuterRef("pk"), status=Narration.Status.READY)
            )
        )
    )
    return quiz.annotate_series_count(reading.annotate(guides, user, "guide"))


def _referentials_for(user):
    queryset = Referential.objects.filter(is_published=True)
    if (
        not settings.THIRD_PARTY_EXAM_OUTLINES
    ):  # programmes d'examen tiers masqués sur cette instance
        queryset = queryset.exclude(Q(kind=Referential.Kind.EXAM) & ~Q(issuer__istartswith="ANSSI"))
    return reading.annotate(queryset, user, "referential")


def _search(queryset, query, fields):
    """Chaque mot doit apparaître dans au moins un des champs (sans tenir compte des accents)."""
    for term in [t for t in query.split() if len(t) > 1][:8]:
        condition = Q()
        for field in fields:
            condition |= Q(**{f"{field}__sans_accent__icontains": term})
        queryset = queryset.filter(condition)
    return queryset


def _next_url(request, default):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, {request.get_host()}, request.is_secure()
    ):
        return target
    return default


def _announce(request, gain, what):
    """Message de félicitations après un gain d'XP (et un éventuel passage de niveau)."""
    bonus = (
        f", +{gain.emeralds} émeraude{'s' if gain.emeralds > 1 else ''}" if gain.emeralds else ""
    )
    if gain.level_up:
        messages.success(
            request,
            f"{what} : +{gain.xp} XP{bonus}. Niveau {gain.level_after} atteint : {gain.title} !",
        )
    elif gain.xp:
        messages.success(request, f"{what} : +{gain.xp} XP{bonus}.")
    else:
        messages.success(request, f"{what}.")


def _serve(field_file, filename, content_type=None):
    if not field_file:
        raise Http404("Fichier absent.")
    content_type = (
        content_type or mimetypes.guess_type(field_file.name)[0] or "application/octet-stream"
    )
    response = FileResponse(field_file.open("rb"), content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(False, filename)
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


# --- Tableau de bord ----------------------------------------------------------------
@login_required
def dashboard(request):
    """Page d'accueil : ce qu'il y a à faire aujourd'hui, la progression et l'actualité cyber."""
    user = request.user
    items = list(ReadingListItem.objects.filter(user=user).select_related("guide", "referential"))
    finished = [i for i in items if i.finished_at]
    queue = reading.queue(user)

    plan = StudyPlan.objects.filter(user=user).first()
    today_reading, next_reading_day = [], None
    if plan and queue:
        first_day = build_schedule(queue, plan.minutes_per_day, weekdays=plan.weekdays,
                                   days_off=vacations.days_off(user, since=timezone.localdate()))[0]
        if first_day.date == timezone.localdate():
            today_reading = first_day.items or [first_day.continued]
        else:  # pas un jour de lecture : le prochain est annoncé
            next_reading_day = first_day.date
    vacation = vacations.current(user)

    tests = _test_entries(user)
    best = (QuizAttempt.objects.filter(user=user).values("guide", "referential", "series")
            .annotate(best=Max("score"), total=Max("total")))
    review = revision.todays_review(user, create=False)
    all_paths = paths.build(_guides_for(user))
    articles = news.latest(NewsItem.Kind.NEWS, 8, per_source=3)
    for article in articles:
        article.source_name = news.SOURCE_NAMES.get(article.source, article.source)
    return render(
        request,
        "catalogue/dashboard.html",
        {
            "stats": {
                "read": len(finished),
                "in_list": len(items),
                "progress": _percent(len(finished), len(items)),
                "series_done": len(best),
                "average": (
                    round(sum(100 * b["best"] / b["total"] for b in best if b["total"]) / len(best))
                    if best
                    else None
                ),
            },
            "plan": plan,
            "today_reading": today_reading,
            "next_reading_day": next_reading_day,
            "vacation": vacation,
            "pending": len(queue),
            "next_reading": [i for i in queue if i not in today_reading][:4],
            "tests_today": tests["today"],
            "paths_current": paths.in_progress(all_paths),
            "path_suggestion": next((p for p in all_paths if not p.started), None),
            "review": review,
            "review_best": review.attempts.order_by("-score").first() if review else None,
            "review_available": review is not None or bool(revision.eligible_questions(user)),
            "alerts": news.latest(NewsItem.Kind.ALERT, 3, max_age_days=30),
            "articles": articles,
            "leaks": news.latest(
                NewsItem.Kind.LEAK, 10
            ),  # autant de hauteur que les actualités, en face
            "news_refreshed": news.last_refresh(),
        },
    )


# --- Recherche ----------------------------------------------------------------------
SEARCH_TABS = [
    ("tout", "Tout"),
    ("guides", "Guides"),
    ("texte", "Dans les guides"),
    ("cours", "Cours"),
    ("podcasts", "Podcasts"),
    ("lexique", "Lexique"),
    ("referentiels", "Référentiels"),
    ("certifications", "Certifications"),
]
SEARCH_FORMATS = {"audio": "Version audio disponible", "qcm": "Avec QCM"}


@login_required
@rate_limit("recherche", 30, 60)
def search(request):
    """
    Recherche dans tout le site : un onglet par type de contenu (`?type=`), « Tout » montre les
    meilleurs résultats de chaque type. Filtres : domaine, niveau, format (guides avec audio ou
    QCM).
    """
    query = request.GET.get("q", "").strip()[:100]
    tab = request.GET.get("type", "tout")
    tab = tab if tab in dict(SEARCH_TABS) else "tout"
    category = request.GET.get("domaine", "")
    category = category if GuideCategory.objects.filter(slug=category).exists() else ""
    level = request.GET.get("niveau", "")
    level = level if level in Level.values else ""
    fmt = request.GET.get("format", "")
    fmt = fmt if fmt in SEARCH_FORMATS else ""
    context = {"query": query, "tab": tab, "tabs": SEARCH_TABS, "formats": SEARCH_FORMATS.items(),
               "categories": GuideCategory.objects.all(), "levels": Level.choices,
               "filters": {"domaine": category, "niveau": level, "format": fmt}}
    words = finder.terms(query)
    if not words:
        return render(request, "catalogue/search.html", context)

    guides = _guides_for(request.user)
    if category:
        guides = guides.filter(category__slug=category)
    if level:
        guides = guides.filter(level=level)
    if fmt == "audio":
        guides = guides.filter(has_audio=True)
    elif fmt == "qcm":
        guides = guides.filter(quiz_series__gt=0)
    found = list(_search(guides, query, SEARCH_FIELDS["guides"])[:200])
    # Pertinence (titre, résumé, thème), puis les guides en français.
    found.sort(key=lambda g: (-finder.score(words, g.title, g.summary, f"{g.theme} {g.collection}"),
                              g.language != Language.FR, g.title))
    referentials = _search(
        _referentials_for(request.user), query, SEARCH_FIELDS["referentials"]
    ).order_by("kind", "name")
    certifications = _ordered_certifications(
        _filter_certifications(
            Certification.objects.filter(is_published=True),
            {"q": query, "domaine": category, "niveau": level},
        )
    )
    other_content = not fmt  # le filtre de format ne concerne que les guides
    results = {
        "guides": found,
        "texte": finder.guide_passages(guides, words),
        "cours": finder.courses(words, category, level) if other_content else [],
        "podcasts": finder.podcasts(words, category, level) if other_content else [],
        "lexique": finder.glossary_entries(guides, words),
        # Les référentiels n'ont ni domaine ni niveau : seulement sans ces filtres.
        "referentiels": list(referentials) if other_content and not (category or level) else [],
        "certifications": list(certifications) if other_content else [],
    }
    counts = {key: len(items) for key, items in results.items()}
    counts["tout"] = sum(counts.values())
    limit = finder.PREVIEW if tab == "tout" else finder.TAB_LIMIT
    shown = {key: items[:limit] for key, items in results.items() if tab in ("tout", key)}
    context.update(
        {
            "results": shown,
            "counts": counts,
            "total": counts["tout"],
            "words": words,
            "limit": limit,
        }
    )
    return render(request, "catalogue/search.html", context)


# --- Parcours de lecture ------------------------------------------------------------
@login_required
def path_list(request):
    """Parcours de lecture par thème et par niveau (filtres communs du catalogue)."""
    filters = _catalogue_filters(request)
    guides = _guides_for(request.user)
    if filters["q"]:
        guides = _search(guides, filters["q"], SEARCH_FIELDS["guides"])
    categories = GuideCategory.objects.all()
    if filters["domaine"]:
        categories = categories.filter(slug=filters["domaine"])
    all_paths = paths.build(guides, categories, level=filters["niveau"])
    custom = (
        ReadingPath.objects.filter(is_published=True)
        .select_related("category")
        .prefetch_related("steps")
    )
    if filters["domaine"]:
        custom = custom.filter(category__slug=filters["domaine"])
    if filters["niveau"]:
        custom = custom.filter(level=filters["niveau"])
    if filters["q"]:
        custom = custom.filter(
            Q(title__icontains=filters["q"]) | Q(description__icontains=filters["q"])
        )
    return render(
        request,
        "catalogue/path_list.html",
        _catalogue_context(
            request,
            filters,
            paths=all_paths,
            current=paths.in_progress(all_paths),
            custom_paths=list(custom),
        ),
    )


def _custom_steps(user, path):
    """Étapes d'un parcours sur mesure, avec l'état de lecture de chaque guide pour
    l'utilisateur."""
    guides = {g.pk: g for g in _guides_for(user).filter(pk__in=path.steps.values("guide"))}
    steps = [s for s in path.steps.all() if s.guide_id in guides]
    for step in steps:
        step.item = guides[step.guide_id]
    return steps


@login_required
def custom_path(request, slug):
    """Parcours conçu à la main dans l'atelier de création : des guides dans un ordre choisi."""
    path = get_object_or_404(
        ReadingPath.objects.select_related("category"), slug=slug, is_published=True
    )
    steps = _custom_steps(request.user, path)
    read = sum(1 for s in steps if s.item.finished_at)
    return render(request, "catalogue/custom_path.html", {
        "path": path, "steps": steps, "read": read, "total": len(steps),
        "percent": round(100 * read / len(steps)) if steps else 0,
        "next_step": next((s for s in steps if not s.item.finished_at), None),
        "to_add": [s for s in steps if not s.item.in_list],
        "minutes": sum(s.item.estimated_minutes or 0 for s in steps),
    })


@login_required
@require_POST
def custom_path_add(request, slug):
    path = get_object_or_404(ReadingPath, slug=slug, is_published=True)
    added = sum(
        reading.add(request.user, step.item)[1] for step in _custom_steps(request.user, path)
    )
    if added:
        messages.success(
            request,
            f"{added} guide{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''} à votre "
            "lecture, dans l'ordre du parcours.",
        )
    else:
        messages.info(request, "Ces guides sont déjà tous dans votre lecture.")
    return redirect("catalogue:custom_path", slug=slug)


@login_required
def path_detail(request, slug):
    """Un parcours : les guides du thème dans l'ordre de lecture, niveau par niveau."""
    category = get_object_or_404(GuideCategory, slug=slug)
    found = paths.build(_guides_for(request.user).filter(category=category), [category])
    if not found:
        raise Http404("Parcours vide.")
    return render(request, "catalogue/path_detail.html", {"path": found[0]})


@login_required
@require_POST
def reading_add_path(request, slug):
    """Ajoute à Ma lecture les guides d'un parcours (ou d'un seul de ses niveaux), dans l'ordre du
    parcours."""
    category = get_object_or_404(GuideCategory, slug=slug)
    level = request.POST.get("niveau", "")
    found = paths.build(_guides_for(request.user).filter(category=category), [category],
                        level=level if level in Level.values else None)
    added = sum(reading.add(request.user, guide)[1] for guide in (found[0].guides if found else []))
    if added:
        messages.success(
            request,
            f"{added} guide{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''} "
            "à votre lecture, dans l'ordre du parcours.",
        )
    else:
        messages.info(request, "Ces guides sont déjà tous dans votre lecture.")
    return redirect(_next_url(request, reverse("catalogue:path_detail", args=[slug])))


# --- Catalogue : guides ------------------------------------------------------------
@login_required
def guide_list(request):
    filters = _catalogue_filters(request)
    guides = _guides_for(request.user)
    if filters["q"]:
        guides = _search(guides, filters["q"], SEARCH_FIELDS["guides"])
    if filters["domaine"]:
        guides = guides.filter(category__slug=filters["domaine"])
    if filters["niveau"]:
        guides = guides.filter(level=filters["niveau"])
    if filters["qcm"] == "avec":
        guides = guides.filter(quiz_series__gt=0)
    elif filters["qcm"] == "sans":
        guides = guides.filter(quiz_series=0)
    if filters["audio"]:
        guides = guides.filter(has_audio=filters["audio"] == "avec")
    guides = guides.order_by("category__position", "level", "estimated_minutes", "title")
    return render(request, "catalogue/guide_list.html", _catalogue_context(
        request, filters,
        guides=[g for g in guides if g.language == Language.FR],
        english_guides=[g for g in guides if g.language != Language.FR]))


@login_required
def guide_detail(request, slug):
    """Fiche d'information du guide, affichée avant l'ouverture du document."""
    guide = get_object_or_404(_guides_for(request.user), slug=slug)

    # Certifications dont un référentiel cite ce guide, avec le renvoi utilisé.
    citations = (GuideCitation.objects.filter(guide=guide, referential__is_published=True)
                 .select_related("referential"))
    cited_by = {}
    for citation in citations:
        for certification in citation.referential.certifications.filter(is_published=True):
            entry = cited_by.setdefault(
                certification.pk, {"certification": certification, "citations": []}
            )
            entry["citations"].append(citation)
    suggested = (Certification.objects.filter(is_published=True, guide_categories=guide.category_id)
                 .exclude(pk__in=cited_by).order_by("level", "name") if guide.category_id else [])

    return render(
        request,
        "catalogue/guide_detail.html",
        {
            "guide": guide,
            "glossary_entries": glossary.published_entries([guide]),
            "has_pdf": quizgen.has_pdf(guide),
            "quiz_series": quiz.series_status(request.user, guide, read=bool(guide.finished_at)),
            "documents": guide.documents.all(),
            "cited_by": sorted(cited_by.values(), key=lambda e: e["certification"].name),
            "suggested_certifications": suggested,
        },
    )


@login_required
def guide_read(request, slug, document_id):
    guide = get_object_or_404(_guides_for(request.user), slug=slug)
    document = get_object_or_404(GuideDocument, pk=document_id, guide=guide)
    if not document.file:
        if document.source_url:
            return redirect(document.source_url)
        raise Http404("Document indisponible.")
    reading.mark_started(request.user, guide)
    return render(request, "catalogue/reader.html", {
        "item": guide, "guide": guide, "kind": "guide", "back_url": guide.get_absolute_url(),
        "glossary_entries": glossary.published_entries([guide]),
        "file_url": reverse("catalogue:guide_document", args=[guide.slug, document.pk]),
        "label": document.label,
    })


# --- Version audio --------------------------------------------------------------------------
@login_required
@rate_limit("audio", 10, 3600, methods=("POST",))
def guide_listen(request, slug):
    """Écouter un guide : lecteur audio par chapitres, ou demande de génération de la version
    audio."""
    guide = get_object_or_404(_guides_for(request.user), slug=slug)
    if request.method == "POST":
        try:
            narration.request(guide, request.user)
        except narration.NarrationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request, "Version audio demandée : elle sera prête dans quelques minutes."
            )
        return redirect("catalogue:guide_listen", slug=slug)

    audio = Narration.objects.filter(guide=guide).first()
    ready = audio is not None and audio.status == Narration.Status.READY
    progress = (
        ListeningProgress.objects.filter(user=request.user, narration=audio).first()
        if ready
        else None
    )
    sections = list(audio.sections.all()) if ready else []
    done = set(progress.sections_done) if progress else set()
    for section in sections:
        section.listened = section.position in done
    return render(
        request,
        "catalogue/listen.html",
        {
            "guide": guide,
            "audio": audio,
            "ready": ready,
            "sections": sections,
            "progress": progress,
            "listening": ready,
            "glossary_entries": glossary.published_entries([guide]),
            "job": (
                generation.job_progress(audio)
                if audio and audio.status == Narration.Status.PROCESSING
                else None
            ),
            "outdated": ready and audio.version != narration_text.VERSION,
            "queue_position": narration.queue_position(audio) if audio else 0,
            "has_document": narration.source_document(guide) is not None,
        },
    )


# --- Assistant de lecture -------------------------------------------------------------------
@login_required
@require_POST
def guide_ask(request, slug=None):
    """
    Question à l'assistant, depuis n'importe quelle page ; sur les pages d'un guide, elle porte
    sur ce guide. La réponse de l'IA arrive au fil de l'eau.
    """
    guide = get_object_or_404(_guides_for(request.user), slug=slug) if slug else None
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Requête invalide."}, status=400)
    try:
        answer = assistant.ask(
            guide,
            request.user,
            str(payload.get("question", "")),
            passage=str(payload.get("passage", "")),
            chapter=str(payload.get("chapter", "")),
            history=payload.get("history"),
        )
    except assistant.AssistantError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    response = StreamingHttpResponse(answer, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"  # nginx : transmettre chaque morceau sans attendre la fin
    return response


# Taille maximale d'un morceau servi pour une requête « Range » : le lecteur redemande la suite ;
# sans cette
# borne, « bytes=0- » chargerait tout le fichier en mémoire à chaque requête.
RANGE_CHUNK = 2 * 1024 * 1024


def _serve_range(request, field_file, content_type):
    """Sert un fichier en tenant compte de l'en-tête Range (nécessaire pour avancer dans un
    audio)."""
    size = field_file.size
    match = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", ""))
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
        else:  # « bytes=-500 » : les 500 derniers octets
            start, end = max(size - int(match.group(2)), 0), size - 1
        end = min(end, start + RANGE_CHUNK - 1)
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        with field_file.open("rb") as handle:
            handle.seek(start)
            data = handle.read(end - start + 1)
        response = HttpResponse(data, status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        response = FileResponse(field_file.open("rb"), content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=86400"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@login_required
def narration_audio(request, slug, position):
    """Fichier MP3 d'une section, réservé aux utilisateurs connectés."""
    section = get_object_or_404(
        NarrationSection,
        narration__guide__slug=slug,
        narration__guide__is_published=True,
        narration__status=Narration.Status.READY,
        position=position,
    )
    if not section.audio:
        raise Http404("Audio indisponible.")
    return _serve_range(request, section.audio, "audio/mpeg")


@login_required
@require_POST
def listen_progress(request, slug):
    """
    Position d'écoute (reprise) et sections écoutées jusqu'au bout. Quand toutes les
    sections l'ont été, le guide est marqué comme lu (XP compris), comme une lecture.
    """
    audio = get_object_or_404(Narration, guide__slug=slug, guide__is_published=True,
                              status=Narration.Status.READY)
    positions = list(audio.sections.values_list("position", flat=True))
    try:
        section = int(request.POST.get("section", ""))
        seconds = max(0.0, min(float(request.POST.get("seconds", "0")), 24 * 3600))
    except ValueError:
        return JsonResponse({"error": "paramètres invalides"}, status=400)
    if section not in positions:
        return JsonResponse({"error": "section inconnue"}, status=400)

    progress, _ = ListeningProgress.objects.get_or_create(user=request.user, narration=audio)
    progress.section, progress.seconds = section, seconds
    if request.POST.get("ended") == "1" and section not in progress.sections_done:
        progress.sections_done = sorted(set(progress.sections_done) | {section})
    reading.mark_started(request.user, audio.guide)
    result = {"done": len(set(progress.sections_done) & set(positions)), "total": len(positions)}
    if not progress.completed_at and set(positions) <= set(progress.sections_done):
        progress.completed_at = timezone.now()
        reading.mark_finished(request.user, audio.guide)
        gain = rewards.reward_reading(request.user, audio.guide)
        result.update(completed=True, xp=gain.xp, emeralds=gain.emeralds)
    progress.save()
    return JsonResponse(result)


@login_required
@xframe_options_sameorigin  # autorise l'affichage dans l'iframe de la page de lecture uniquement
def guide_document(request, slug, document_id):
    """Sert le PDF uniquement aux utilisateurs connectés (pas d'accès public aux médias)."""
    document = get_object_or_404(GuideDocument, pk=document_id, guide__slug=slug,
                                 guide__is_published=True)
    return _serve(document.file, f"{slug}-{document.pk}.pdf", "application/pdf")


@login_required
def guide_cover(request, slug):
    guide = get_object_or_404(Guide, slug=slug, is_published=True)
    return _serve(guide.cover, f"{slug}.avif", "image/avif")


# --- Ma lecture ----------------------------------------------------------------------
def _document_from_post(request):
    kind, slug = request.POST.get("kind"), request.POST.get("slug", "")
    if kind == "guide":
        return get_object_or_404(Guide, slug=slug, is_published=True)
    if kind == "referentiel":
        return get_object_or_404(Referential, slug=slug, is_published=True)
    raise Http404("Type de document inconnu.")


@login_required
def reading_list(request):
    items = list(ReadingListItem.objects.filter(user=request.user)
                 .select_related("guide", "guide__category", "referential"))
    to_read = [i for i in items if not i.finished_at]
    finished = sorted(
        (i for i in items if i.finished_at), key=lambda i: i.finished_at, reverse=True
    )
    counts = quiz.series_counts([i.guide_id for i in items if i.guide_id])
    for item in items:
        if item.guide_id:
            item.guide.quiz_series = counts.get(item.guide_id, 0)
    return render(request, "catalogue/reading_list.html", {
        "to_read": to_read, "finished": finished, "total": len(items),
        "progress": _percent(len(finished), len(items)),
        "hours_left": round(sum(i.estimated_minutes for i in to_read) / 60, 1),
    })


@login_required
@require_POST
def reading_add(request):
    document = _document_from_post(request)
    _, created = reading.add(request.user, document)
    if created:
        messages.success(request, f"« {document.title} » est ajouté à votre lecture.")
    return redirect(_next_url(request, reverse("catalogue:reading_list")))


@login_required
@require_POST
def reading_add_certification(request, slug):
    """Ajoute d'un coup les guides cités par les référentiels d'une certification."""
    certification = get_object_or_404(Certification, slug=slug, is_published=True)
    guides = (Guide.objects.filter(is_published=True,
                                   citations__referential__certifications=certification)
              .distinct().order_by("level", "estimated_minutes"))
    added = sum(reading.add(request.user, guide)[1] for guide in guides)
    messages.success(
        request,
        f"{added} guide{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''} "
        "à votre lecture.",
    )
    return redirect(_next_url(request, certification.get_absolute_url()))


@login_required
@require_POST
def reading_finish(request):
    """« J'ai lu ce document » depuis sa fiche ou la page de lecture."""
    document = _document_from_post(request)
    reading.mark_finished(request.user, document)
    _announce(
        request,
        rewards.reward_reading(request.user, document),
        f"« {document.title} » est marqué comme lu",
    )
    return redirect(_next_url(request, document.get_absolute_url()))


@login_required
@require_POST
def reading_item(request, pk):
    item = get_object_or_404(ReadingListItem, pk=pk, user=request.user)
    action = request.POST.get("action")
    if action == "monter":
        reading.move(item, -1)
    elif action == "descendre":
        reading.move(item, 1)
    elif action == "lu":
        reading.mark_finished(request.user, item.document)
        _announce(request, rewards.reward_reading(request.user, item.document),
                  f"« {item.document.title} » est marqué comme lu")
    elif action == "a-lire":
        reading.mark_unfinished(item)
    elif action == "retirer":
        item.delete()
        messages.info(request, f"« {item.document.title} » est retiré de votre lecture.")
    else:
        raise Http404("Action inconnue.")
    return redirect(_next_url(request, reverse("catalogue:reading_list")))


@login_required
def study_plan(request):
    plan = StudyPlan.objects.filter(user=request.user).first()
    form = StudyPlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        plan = form.save(commit=False)
        plan.user = request.user
        plan.save()
        messages.success(request, "Votre planning a été recalculé.")
        return redirect("catalogue:study_plan")

    queue = reading.queue(request.user)
    today = timezone.localdate()
    context = {
        "form": form,
        "plan": plan,
        "remaining": len(queue),
        "vacation": vacations.current(request.user),
        "this_week": today - timedelta(days=today.weekday()),
    }
    if plan and queue:
        days = build_schedule(
            queue,
            plan.minutes_per_day,
            today=timezone.localdate(),
            weekdays=plan.weekdays,
            days_off=vacations.days_off(request.user, since=timezone.localdate()),
        )
        context.update({
            "today": timezone.localdate(), "tomorrow": timezone.localdate() + timedelta(days=1),
            "days": days,
            "hours_left": round(sum(i.estimated_minutes for i in queue) / 60, 1),
            "finish": days[-1].date,
        })
    return render(request, "catalogue/study_plan.html", context)


@login_required
def study_plan_ics(request):
    """Planning de lecture à importer dans son agenda (Thunderbird, Google Agenda, Proton
    Calendar…)."""
    plan = get_object_or_404(StudyPlan, user=request.user)
    queue = reading.queue(request.user)
    days = (
        build_schedule(
            queue,
            plan.minutes_per_day,
            today=timezone.localdate(),
            weekdays=plan.weekdays,
            days_off=vacations.days_off(request.user, since=timezone.localdate()),
        )
        if queue
        else []
    )
    content = calendar_export.build_ics(
        days,
        plan.reading_time,
        plan.minutes_per_day,
        url_for=lambda doc: request.build_absolute_uri(doc.get_absolute_url()),
        planning_url=request.build_absolute_uri(reverse("catalogue:study_plan")),
        uid_owner=f"cyberlearning-lecture-{request.user.pk}",
        uid_domain=request.get_host().split(":")[0],
        instance_name=settings.INSTANCE_NAME,
    )
    response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, "cyberlearning-planning.ics")
    response["Cache-Control"] = "no-store"
    return response


# --- Tests de connaissances --------------------------------------------------------------
def _test_entries(user):
    """Guides lus, classés selon l'état de leur test : à faire aujourd'hui, demain, terminés, sans
    questions."""
    read_items = (
        ReadingListItem.objects.filter(user=user, guide__isnull=False, finished_at__isnull=False)
        .select_related("guide")
        .order_by("-finished_at")
    )
    numbers = {}
    for guide_id, series in (QuizQuestion.objects.filter(guide__in=[i.guide_id for i in read_items])
                             .values_list("guide_id", "series").distinct().order_by("series")):
        numbers.setdefault(guide_id, []).append(series)

    today, upcoming, finished, waiting = [], [], [], []
    for item in read_items:
        if item.guide_id not in numbers:
            waiting.append(item.guide)
            continue
        series = quiz.series_status(user, item.guide, numbers[item.guide_id], read=True)
        entry = {"guide": item.guide, "series": series,
                 "next": next((s for s in series if s.state == quiz.AVAILABLE), None)}
        if entry["next"]:
            today.append(entry)
        elif all(s.state == quiz.DONE for s in series):
            finished.append(entry)
        else:
            upcoming.append(entry)
    return {"today": today, "upcoming": upcoming, "finished": finished, "waiting": waiting}


@login_required
def tests_overview(request):
    """Mes tests : les guides lus et l'état de leurs séries de questions."""
    review = revision.todays_review(request.user, create=False)
    return render(request, "catalogue/tests_overview.html", {
        **_test_entries(request.user),
        "review": review,
        "review_best": review.attempts.order_by("-score").first() if review else None,
        "review_available": review is not None or bool(revision.eligible_questions(request.user)),
        "review_streak": revision.streak_days(request.user),
    })


@login_required
def daily_review(request, number=1):
    """Révision du jour (ou seconde révision achetée) : questions mélangées de tous les guides
    lus."""
    if number not in (1, 2):
        raise Http404("Révision inconnue.")
    if number == 2 and not shop.has_second_review(request.user, timezone.localdate()):
        messages.info(
            request, "La seconde révision s'achète une fois la révision du jour terminée."
        )
        return redirect("catalogue:daily_review")
    review = revision.todays_review(request.user, number=number)
    if review is None:
        messages.info(
            request,
            "La révision du jour s'ouvre dès que vous avez fait au moins une série de test.",
        )
        return redirect("catalogue:tests_overview")
    questions = revision.review_questions(review)
    if not questions:
        raise Http404("Révision vide.")

    attempt = gain = None
    if request.method == "POST":
        score, answers = quiz.grade(questions, request.POST)
        attempt = DailyReviewAttempt.objects.create(
            review=review, score=score, total=len(questions), answers=answers
        )
        gain = rewards.reward_review(request.user, review, questions, answers)
        for question in questions:
            question.given = answers[str(question.pk)]
    second_bought = shop.has_second_review(request.user, review.date)
    return render(
        request,
        "catalogue/daily_review.html",
        {
            "review": review,
            "questions": questions,
            "attempt": attempt,
            "gain": gain,
            "no_assistant": (
                attempt is None
            ),  # pas d'aide pendant le test ; elle revient avec les résultats
            "guide_count": len({q.guide_id for q in questions}),
            "streak": revision.streak_days(request.user),
            "second_review": items.get(items.SECOND_REVIEW),
            "second_bought": second_bought,
            "second_possible": (
                number == 1
                and attempt is not None
                and not second_bought
                and revision.second_review_possible(request.user)
            ),
        },
    )


@login_required
@require_POST
def buy_second_review(request):
    """Achat de la seconde révision du jour (en émeraudes), une fois la première faite."""
    if not revision.second_review_possible(request.user):
        messages.error(
            request,
            "Faites d'abord la révision du jour ; il faut aussi assez de nouvelles questions.",
        )
        return redirect("catalogue:daily_review")
    try:
        shop.buy_item(request.user, items.SECOND_REVIEW, day=timezone.localdate())
    except shop.ShopError as exc:
        messages.error(request, str(exc))
        return redirect("catalogue:daily_review")
    return redirect("catalogue:daily_review_number", number=2)


@login_required
def quiz_take(request, slug, number):
    return _quiz_take(request, get_object_or_404(Guide, slug=slug, is_published=True), number)


@login_required
def referential_quiz_take(request, slug, number):
    return _quiz_take(
        request, get_object_or_404(_referentials_for(request.user), slug=slug), number
    )


def _quiz_take(request, guide, number):
    """Série du test d'un guide ou d'un référentiel ANSSI (`guide` : l'un ou l'autre)."""
    is_referential = isinstance(guide, Referential)
    if not quiz.is_read(request.user, guide):
        messages.info(
            request,
            f"Lisez d'abord ce {'référentiel' if is_referential else 'guide'} : "
            "son test se débloque une fois la lecture terminée.",
        )
        return redirect(guide.get_absolute_url())
    if not quiz.can_take(request.user, guide, number):
        messages.info(
            request,
            "Cette série n'est pas encore ouverte : une nouvelle série s'ouvre chaque jour.",
        )
        return redirect("catalogue:tests_overview")
    questions = list(QuizQuestion.objects.filter(**subject_filter(guide), series=number))
    if not questions:
        raise Http404("Série introuvable.")

    attempt = gain = None
    if request.method == "POST":
        score, answers = quiz.grade(questions, request.POST)
        attempt = QuizAttempt.objects.create(
            user=request.user,
            **subject_filter(guide),
            series=number,
            score=score,
            total=len(questions),
            answers=answers,
        )
        gain = rewards.reward_series(request.user, guide, number, questions, answers)
        for question in questions:
            question.given = answers[str(question.pk)]
    total_series = len(quiz.series_numbers(guide))
    if number < total_series:
        result_message = (
            f"La série {number + 1} s'ouvrira le lendemain de votre première tentative. "
            "Vous pouvez refaire cette série quand vous le souhaitez."
        )
    else:
        result_message = (
            f"C'était la dernière série de ce {'référentiel' if is_referential else 'guide'}. "
            "Vous pouvez la refaire quand vous le souhaitez."
        )
    retake = "catalogue:referential_quiz_take" if is_referential else "catalogue:quiz_take"
    return render(
        request,
        "catalogue/quiz.html",
        {
            "guide": guide,
            "is_referential": is_referential,
            "retake_url": reverse(retake, args=[guide.slug, number]),
            "number": number,
            "total_series": total_series,
            "no_assistant": (
                attempt is None
            ),  # pas d'aide pendant le test ; elle revient avec les résultats
            "questions": questions,
            "attempt": attempt,
            "gain": gain,
            "result_message": result_message,
        },
    )


# --- Référentiels et grilles d'exigences ----------------------------------------------
def _referential_rows(request, queryset):
    tracked = Q(requirements__kind__in=Requirement.TRACKED_KINDS)
    referentials = (queryset
                    .annotate(total=Count("requirements", filter=tracked, distinct=True),
                              done=Count("requirements", distinct=True, filter=tracked & Q(
                                  requirements__progress__user=request.user,
                                  requirements__progress__status__in=["done", "na"])))
                    .order_by("kind", "name"))
    for referential in referentials:
        referential.progress = _percent(referential.done, referential.total)
    return referentials


@login_required
def referential_list(request):
    """Référentiels ANSSI et normes (les programmes d'examen ont leur propre onglet)."""
    filters = _catalogue_filters(request)
    queryset = _filter_referentials(
        _referentials_for(request.user).exclude(kind=Referential.Kind.EXAM), filters
    )
    groups = {}
    for referential in _referential_rows(request, queryset):
        groups.setdefault(referential.get_kind_display(), []).append(referential)
    return render(
        request,
        "catalogue/referential_list.html",
        _catalogue_context(request, filters, groups=groups),
    )


@login_required
def exam_list(request):
    """Programmes d'examen des certifications professionnelles."""
    filters = _catalogue_filters(request)
    queryset = _filter_referentials(
        _referentials_for(request.user).filter(kind=Referential.Kind.EXAM), filters
    )
    return render(
        request,
        "catalogue/exam_list.html",
        _catalogue_context(
            request,
            filters,
            referentials=_referential_rows(request, queryset.prefetch_related("certifications")),
        ),
    )


@login_required
def glossary_index(request):
    """Lexique général : sigles et termes de tous les guides publiés, par ordre alphabétique."""
    filters = _catalogue_filters(request)
    entries = GlossaryEntry.objects.filter(glossary__status=Glossary.Status.PUBLISHED,
                                           glossary__guide__in=_guides_for(request.user)) \
        .select_related("glossary__guide").order_by("key", "glossary__published_at")
    if filters["q"]:
        entries = entries.filter(
            Q(term__icontains=filters["q"])
            | Q(expansion__icontains=filters["q"])
            | Q(definition__icontains=filters["q"])
        )
    if filters["domaine"]:
        entries = entries.filter(glossary__guide__category__slug=filters["domaine"])
    grouped = {}
    for entry in entries:
        item = grouped.setdefault(entry.key, {"entry": entry, "guides": []})
        if entry.glossary.guide not in item["guides"]:
            item["guides"].append(entry.glossary.guide)
    letters = {}
    for key, item in grouped.items():
        letter = key[:1].upper() if key[:1].isalpha() else "#"
        letters.setdefault(letter, []).append(item)
    return render(request, "catalogue/glossary_index.html", _catalogue_context(
        request, filters, letters=sorted(letters.items()), total=len(grouped)))


@login_required
def referential_detail(request, slug):
    referential = get_object_or_404(_referentials_for(request.user), slug=slug)
    statuses = dict(RequirementProgress.objects
                    .filter(user=request.user, requirement__referential=referential)
                    .values_list("requirement_id", "status"))

    rows = list(referential.requirements.all())
    checks = {}
    for row in rows:
        if row.parent_id:
            checks.setdefault(row.parent_id, []).append(row)

    status_filter = request.GET.get("statut", "")
    sections, total, done = [], 0, 0
    for row in rows:
        if row.parent_id:
            continue
        row.status = statuses.get(row.pk, RequirementProgress.Status.TODO)
        row.checks_list = checks.get(row.pk, [])
        total += 1
        done += row.status in ("done", "na")
        if status_filter and row.status != status_filter:
            continue
        if not sections or sections[-1]["theme"] != row.theme:
            sections.append({"theme": row.theme, "rows": []})
        sections[-1]["rows"].append(row)

    citations = list(referential.citations.select_related("guide"))
    quiz_guides = (
        _guides_for(request.user)
        .filter(citations__referential=referential, quiz_series__gt=0)
        .distinct()
    )
    return render(
        request,
        "catalogue/referential_detail.html",
        {
            "referential": referential,
            "sections": sections,
            "citations": citations,
            "quiz_guides": quiz_guides,
            "quiz_series": quiz.series_status(
                request.user, referential, read=bool(referential.finished_at)
            ),
            "can_generate_quiz": (
                referential.kind == Referential.Kind.ANSSI and quizgen.has_pdf(referential)
            ),
            "total": total,
            "done": done,
            "progress": _percent(done, total),
            "statuses": RequirementProgress.Status.choices,
            "status_filter": status_filter,
            "certifications": referential.certifications.filter(is_published=True),
        },
    )


@login_required
def referential_read(request, slug):
    referential = get_object_or_404(_referentials_for(request.user), slug=slug)
    if not referential.document:
        raise Http404("Document indisponible.")
    reading.mark_started(request.user, referential)
    return render(request, "catalogue/reader.html", {
        "item": referential, "kind": "referentiel", "back_url": referential.get_absolute_url(),
        "file_url": reverse("catalogue:referential_document", args=[referential.slug]),
        "label": referential.name,
    })


@login_required
@xframe_options_sameorigin
def referential_document(request, slug):
    referential = get_object_or_404(_referentials_for(request.user), slug=slug)
    return _serve(referential.document, f"{slug}.pdf", "application/pdf")


@login_required
def referential_trame(request, slug):
    """Trame d'évaluation Excel officielle de l'ANSSI."""
    referential = get_object_or_404(_referentials_for(request.user), slug=slug)
    response = _serve(referential.trame, f"trame-{slug}.xlsx", XLSX_TYPE)
    response["Content-Disposition"] = content_disposition_header(
        True, f"trame-evaluation-{slug}.xlsx"
    )
    return response


@login_required
def referential_export(request, slug):
    """Grille du référentiel au format Excel, avec la progression de l'utilisateur."""
    referential = get_object_or_404(_referentials_for(request.user), slug=slug)
    statuses = dict(RequirementProgress.objects
                    .filter(user=request.user, requirement__referential=referential)
                    .values_list("requirement_id", "status"))
    response = HttpResponse(grid_workbook(referential, statuses), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition_header(True, f"grille-{slug}.xlsx")
    return response


@login_required
@require_POST
def requirement_status(request, pk):
    requirement = get_object_or_404(Requirement, pk=pk, referential__is_published=True,
                                    kind__in=Requirement.TRACKED_KINDS)
    status = request.POST.get("status", "")
    if status not in RequirementProgress.Status.values:
        raise Http404("Statut inconnu.")
    RequirementProgress.objects.update_or_create(
        user=request.user, requirement=requirement, defaults={"status": status})
    url = reverse("catalogue:referential_detail", args=[requirement.referential.slug])
    query = request.POST.get("statut_filtre", "")
    if query in RequirementProgress.Status.values:
        url += f"?statut={query}"
    return redirect(f"{url}#req-{requirement.pk}")


# --- Certifications -----------------------------------------------------------------
@login_required
def certification_list(request):
    filters = _catalogue_filters(request)
    certifications = _filter_certifications(
        Certification.objects.filter(is_published=True), filters
    )
    return render(request, "catalogue/certification_list.html", _catalogue_context(
        request, filters, certifications=_ordered_certifications(certifications)))


@login_required
def certification_detail(request, slug):
    certification = get_object_or_404(Certification, slug=slug, is_published=True)
    referentials = _referentials_for(request.user).filter(certifications=certification)

    # Guides cités par les référentiels de la certification (avec l'état de lecture).
    citations = (GuideCitation.objects.filter(referential__in=referentials)
                 .select_related("referential").order_by("referential__name", "position"))
    codes = {}
    for citation in citations:
        if citation.guide_id:
            codes.setdefault(citation.guide_id, set()).add(
                citation.code or citation.referential.short_name
            )
    cited_guides = list(_guides_for(request.user).filter(pk__in=codes).order_by("level", "title"))
    for guide in cited_guides:
        guide.codes = sorted(c for c in codes[guide.pk] if c)

    return render(request, "catalogue/certification_detail.html", {
        "certification": certification,
        "referentials": referentials,
        "cited_guides": cited_guides,
        "cited_read": sum(1 for g in cited_guides if g.finished_at),
        "missing_from_list": sum(1 for g in cited_guides if not g.in_list),
        "other_citations": [c for c in citations if not c.guide_id],
        "categories": certification.guide_categories.all(),
    })
