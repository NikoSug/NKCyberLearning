"""
Relecture des contenus générés par l'IA (QCM, cours, podcasts, lexiques) : réservée aux
gestionnaires de contenu.

Chaque question proposée est acceptée, corrigée (puis acceptée) ou rejetée ; les
rejetées peuvent être régénérées ; le QCM est publié quand chaque série compte
assez de questions acceptées.
"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from courses import coursegen
from studio.permissions import author_required
from courses.models import Course
from courses.stats import course_stats
from podcasts import podcastgen
from podcasts.models import Episode, Series
from podcasts.stats import podcast_stats

from . import batches, diagrams, generation, glossary, narration, prereview, queues, quizgen
from .models import (
    Glossary,
    GlossaryEntry,
    Guide,
    Narration,
    QuizDraft,
    QuizDraftQuestion,
    ServiceCommand,
)

Status = QuizDraft.Status
Decision = QuizDraftQuestion.Decision
Prereview = QuizDraftQuestion.Prereview


def content_manager_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_content_manager:
            raise PermissionDenied("Réservé aux gestionnaires de contenu.")
        return view(request, *args, **kwargs)
    return wrapper


def _with_counts(queryset):
    return queryset.select_related("guide", "referential").annotate(
        total=Count("questions"),
        accepted=Count("questions", filter=Q(questions__decision=Decision.ACCEPTED)),
        rejected=Count("questions", filter=Q(questions__decision=Decision.REJECTED)),
        flagged=Count(
            "questions",
            filter=(Q(questions__cross_check=False) | Q(questions__prereview=Prereview.DOUBT))
            & ~Q(questions__decision=Decision.REJECTED),
        ),
    )


REVIEW_TYPES = [
    ("tout", "Tout"),
    ("qcm", "QCM"),
    ("cours", "Cours"),
    ("podcast", "Podcasts"),
    ("lexique", "Lexiques"),
]


def review_counts():
    """Éléments à relire par type (menu du compte et onglets de la relecture)."""
    counts = {"qcm": QuizDraft.objects.filter(status=Status.REVIEW).count(),
              "cours": Course.objects.filter(status=Course.Status.REVIEW).count(),
              "podcast": Series.objects.filter(status=Series.Status.REVIEW).count(),
              "lexique": Glossary.objects.filter(status=Glossary.Status.REVIEW).count()}
    counts["tout"] = sum(counts.values())
    return counts


def _rows(kind):
    """Lignes homogènes (titre, détail, lien, date…) pour un type de contenu, par état."""
    if kind == "qcm":
        drafts = _with_counts(QuizDraft.objects.all())
        def row(d):
            return {
                "kind": kind,
                "label": "QCM",
                "title": str(d.subject),
                "status": d.status,
                "progress": d.progress,
                "error": d.error,
                "when": d.generated_at or d.requested_at,
                "url": reverse("catalogue:review_detail", args=[d.pk]),
                "retry_url": reverse("catalogue:review_draft", args=[d.pk]),
                "published_at": d.published_at,
                "detail": f"{d.total} questions · {d.accepted} acceptée(s)",
                "flag": d.flagged,
            }
        items, statuses = drafts, Status
    elif kind == "cours":
        courses = Course.objects.select_related("category", "guide", "author").annotate(
            lessons_n=Count("lessons", distinct=True), questions_n=Count("questions", distinct=True)
        )
        def row(c):
            return {
                "kind": kind,
                "label": "Cours",
                "title": c.title,
                "status": c.status,
                "progress": c.progress,
                "error": c.error,
                "when": c.generated_at or c.requested_at,
                "url": reverse("courses:review_course", args=[c.pk]),
                "retry_url": reverse("courses:review_course_action", args=[c.pk]),
                "published_at": c.published_at,
                "detail": (
                    f"{'Écrit par ' + c.author.username if c.author else c.get_kind_display()} · "
                    f"{c.lessons_n} leçons · {c.questions_n} questions"
                ),
                "flag": len((c.prereview or {}).get("notes", [])),
            }
        items, statuses = courses, Course.Status
    elif kind == "podcast":
        series = Series.objects.select_related("category", "author").annotate(
            episodes_n=Count("episodes")
        )
        flags = {}
        for series_id, episode_flags in Episode.objects.exclude(flags=[]).values_list(
            "series", "flags"
        ):
            flags[series_id] = flags.get(series_id, 0) + len(episode_flags)
        def row(s):
            return {
                "kind": kind,
                "label": "Podcast",
                "title": s.title,
                "status": s.status,
                "progress": s.progress,
                "error": s.error,
                "when": s.generated_at or s.requested_at,
                "url": reverse("podcasts:review_series", args=[s.pk]),
                "retry_url": reverse("podcasts:review_series_action", args=[s.pk]),
                "published_at": s.published_at,
                "detail": (
                    f"{s.category.name} · {s.episodes_n} épisodes"
                    + (f" · écrit par {s.author.username}" if s.author else "")
                ),
                "flag": flags.get(s.pk, 0),
            }
        items, statuses = series, Series.Status
    else:
        glossaries = Glossary.objects.select_related("guide").annotate(entries_n=Count("entries"))
        def row(g):
            return {
                "kind": kind,
                "label": "Lexique",
                "title": g.guide.title,
                "status": g.status,
                "progress": g.progress,
                "error": g.error,
                "when": g.generated_at or g.requested_at,
                "url": reverse("catalogue:glossary_review", args=[g.pk]),
                "retry_url": reverse("catalogue:glossary_review_action", args=[g.pk]),
                "published_at": g.published_at,
                "detail": f"{g.entries_n} entrées",
                "flag": 0,
            }
        items, statuses = glossaries, Glossary.Status
    def running(i):
        return {**row(i), "job": generation.job_progress(i)}
    return {
        "review": [row(i) for i in items.filter(status=statuses.REVIEW)],
        "in_progress": [
            running(i)
            for i in items.filter(
                status__in=[
                    statuses.PENDING,
                    statuses.GENERATING,
                    "a_enregistrer",
                    "enregistrement",
                ]
            ).order_by("-status", "requested_at")[:30]
        ],  # « generation » avant « attente » : en cours d'abord
        "failed": [
            row(i) for i in items.filter(status=statuses.FAILED).order_by("-requested_at")[:20]
        ],
        "published": [
            row(i) for i in items.filter(status=statuses.PUBLISHED).order_by("-published_at")[:10]
        ],
    }


@content_manager_required
def review_list(request):
    """
    Relecture de tout ce que génère l'IA locale, dans une même fenêtre : QCM, cours et lexiques
    (onglets `?type=`). « Tout » rassemble les éléments à relire, les plus anciens d'abord.
    """
    kind = request.GET.get("type", "tout")
    kind = kind if kind in dict(REVIEW_TYPES) else "tout"
    context = {
        "kind": kind,
        "types": REVIEW_TYPES,
        "counts": review_counts(),
        "model": quizgen.model_name(),
    }
    if kind == "tout":
        rows = [r for k in ("qcm", "cours", "podcast", "lexique") for r in _rows(k)["review"]]
        context["to_review"] = sorted(rows, key=lambda r: r["when"])
    else:
        context.update(_rows(kind))
        context["to_review"] = sorted(context.pop("review"), key=lambda r: r["when"])
        context["missing"] = batches.missing(kind)
        context["unit"] = batches.KINDS[kind][0]
    return render(request, "catalogue/review_list.html", context)


def _question_url(question):
    return reverse("catalogue:review_detail", args=[question.draft_id]) + f"?q={question.pk}"


def _next_to_review(question):
    """Prochaine question à valider après celle-ci (en reprenant au début), ou None s'il n'y en a
    plus."""
    pending = list(
        question.draft.questions.filter(decision=Decision.PENDING).exclude(pk=question.pk)
    )
    after = [q for q in pending if (q.series, q.position) > (question.series, question.position)]
    return (after or pending or [None])[0]


@content_manager_required
def review_detail(request, pk):
    """Relecture d'un brouillon, une question à la fois (par défaut : la première encore à
    valider)."""
    draft = get_object_or_404(QuizDraft.objects.select_related("guide", "referential"), pk=pk)
    questions = list(draft.questions.all())
    if not questions:
        return render(request, "catalogue/review_detail.html", {"draft": draft, "questions": []})
    current = next((q for q in questions if str(q.pk) == request.GET.get("q")), None) \
        or next((q for q in questions if q.decision == Decision.PENDING), questions[0])
    index = questions.index(current)
    accepted = quizgen.accepted_by_series(draft)
    series = [{"number": n, "level": quizgen.LEVELS[n], "accepted": accepted[n],
               "questions": [q for q in questions if q.series == n]} for n in quizgen.SERIES]
    return render(request, "catalogue/review_detail.html", {
        "draft": draft, "questions": questions, "series": series, "current": current,
        "index": index + 1, "previous": questions[index - 1] if index else None,
        "next": questions[index + 1] if index + 1 < len(questions) else None,
        "level": quizgen.LEVELS[current.series], "series_accepted": accepted[current.series],
        "can_publish": quizgen.can_publish(draft), "per_series": quizgen.PER_SERIES,
        "pending": sum(q.decision == Decision.PENDING for q in questions),
        "accepted": sum(q.decision == Decision.ACCEPTED for q in questions),
        "rejected": sum(q.decision == Decision.REJECTED for q in questions),
        "prereview_summary": _prereview_summary(draft, questions),
    })


def _prereview_summary(draft, questions):
    """Bilan du pré-relecteur IA en tête de la relecture (vide s'il n'est pas passé)."""
    if not draft.prereviewed_at:
        return []
    doubtful = sum(q.prereview == Prereview.DOUBT for q in questions)
    regenerated = sum(q.prereview_attempts > 0 for q in questions)
    unread = sum(
        q.prereview == Prereview.NONE and q.decision == Decision.PENDING for q in questions
    )
    lines = [
        (
            f"{doubtful} question{'s' if doubtful > 1 else ''}"
            f" douteuse{'s' if doubtful > 1 else ''} à regarder de près, {regenerated}"
            f" régénérée{'s' if regenerated > 1 else ''} d'office."
            if doubtful or regenerated
            else "Rien à signaler sur les questions relues."
        )
    ]
    if unread:
        lines.append(f"{unread} question(s) sans avis du pré-relecteur.")
    return lines + list(draft.prereview_notes)


@content_manager_required
@require_POST
def review_question(request, pk):
    """Accepter, rejeter ou corriger une question."""
    question = get_object_or_404(QuizDraftQuestion.objects.select_related("draft"), pk=pk)
    if question.draft.status != Status.REVIEW:
        messages.error(request, "Ce brouillon n'est plus en relecture.")
        return redirect(_question_url(question))
    action = request.POST.get("action")
    if action == "rejeter":
        question.decision = Decision.REJECTED
    elif action in ("accepter", "enregistrer"):
        if action == "enregistrer":
            text = request.POST.get("text", "").strip()
            choices = [request.POST.get("correct", "").strip()] + [
                request.POST.get(f"wrong_{i}", "").strip() for i in range(1, 5)]
            choices = [c for c in choices if c]
            if len(text) < 10 or not request.POST.get("correct", "").strip() or len(choices) < 3 \
                    or len({c.lower() for c in choices}) != len(choices):
                messages.error(
                    request,
                    "Il faut un énoncé, une bonne réponse et au moins deux mauvaises réponses, "
                    "toutes différentes.",
                )
                return redirect(_question_url(question))
            diagram = request.POST.get("diagram", "").strip()
            diagram_problem = diagrams.problem(diagram)
            if diagram_problem:
                messages.error(request, f"Schéma non enregistré : {diagram_problem}.")
                return redirect(_question_url(question))
            question.text, question.choices = text[:500], [c[:300] for c in choices]
            question.diagram = diagram
            question.explanation = request.POST.get("explanation", "").strip()[:1000]
            question.source = request.POST.get("source", "").strip()[:120]
            question.edited = True
        question.decision = Decision.ACCEPTED
    elif action == "a-valider":
        question.decision = Decision.PENDING
    else:
        messages.error(request, "Action inconnue.")
        return redirect(_question_url(question))
    question.save()
    if question.decision == Decision.PENDING:
        return redirect(_question_url(question))
    following = _next_to_review(question)  # on enchaîne sur la prochaine question à valider
    if following is None:
        messages.success(
            request,
            "Toutes les questions ont été traitées."
            + (
                " Le QCM peut être publié."
                if quizgen.can_publish(question.draft)
                else (
                    " Il manque des questions acceptées dans certaines séries : régénérez les"
                    " rejetées."
                )
            ),
        )
        return redirect(_question_url(question))
    return redirect(_question_url(following))


@content_manager_required
@require_POST
def review_draft(request, pk):
    """Actions sur un brouillon entier : tout accepter (vérifiées), régénérer les rejetées, publier,
    abandonner."""
    draft = get_object_or_404(QuizDraft, pk=pk)
    action = request.POST.get("action")
    if (
        action == "relancer"
        and draft.status in (Status.FAILED, Status.REVIEW)
        and not quizgen.has_pdf(draft.subject)
    ):
        draft.status = Status.DISCARDED
        draft.save(update_fields=["status"])
        messages.error(
            request,
            f"« {draft.subject} » n'a pas de document PDF : pas de QCM possible, "
            "le brouillon est abandonné.",
        )
        return redirect("catalogue:review_list")
    if action == "relancer" and draft.status in (Status.FAILED, Status.REVIEW):
        draft.status, draft.error = Status.PENDING, ""
        draft.save(update_fields=["status", "error"])
        messages.success(
            request,
            "Brouillon remis en file : les questions rejetées ou manquantes seront régénérées.",
        )
        return redirect("catalogue:review_list")
    if draft.status != Status.REVIEW:
        messages.error(request, "Ce brouillon n'est pas en relecture.")
    elif action == "accepter-verifiees":
        count = (
            draft.questions.filter(decision=Decision.PENDING, cross_check=True)
            .exclude(prereview=Prereview.DOUBT)
            .update(decision=Decision.ACCEPTED)
        )
        messages.success(
            request,
            f"{count} question(s) vérifiée(s) par l'IA acceptée(s). Relisez les autres"
            " (dont celles que le pré-relecteur juge douteuses).",
        )
    elif action == "publier":
        try:
            path = quizgen.publish(draft, request.user)
        except quizgen.GenerationError as exc:
            messages.error(request, str(exc))
        else:
            note = f" Fichier enregistré : catalogue/data/quiz/{path.name}." if path else ""
            messages.success(
                request, f"QCM de « {draft.subject} » publié : il est visible des apprenants.{note}"
            )
            return redirect("catalogue:review_list")
    elif action == "abandonner":
        draft.status = Status.DISCARDED
        draft.save(update_fields=["status"])
        messages.info(request, "Brouillon abandonné.")
        return redirect("catalogue:review_list")
    else:
        messages.error(request, "Action inconnue.")
    return redirect("catalogue:review_detail", pk=draft.pk)


@content_manager_required
@require_POST
def review_request(request, slug, model=Guide):
    """Demande la génération du QCM d'un guide ou d'un référentiel ANSSI (depuis sa fiche)."""
    guide = get_object_or_404(model, slug=slug)
    try:
        quizgen.request(guide, request.user)
    except quizgen.GenerationError as exc:
        messages.error(request, f"« {guide} » : {exc}.")
        return redirect(guide.get_absolute_url())
    messages.success(request, f"Génération du QCM de « {guide} » demandée.")
    return redirect("catalogue:review_list")


# --- Tableau de bord des générations (QCM par IA, cours par IA, versions audio) -------------
def _glossary_action(request, action):
    if action == "relancer":
        item = get_object_or_404(
            Glossary, pk=request.POST.get("item"), status=Glossary.Status.FAILED
        )
        glossary.reset(item, request.user)
        messages.success(request, f"Lexique de « {item.guide.title} » remis en file.")
    elif action == "relancer-echecs":
        failed = list(Glossary.objects.filter(status=Glossary.Status.FAILED))
        for item in failed:
            glossary.reset(item, request.user)
        messages.success(request, f"{len(failed)} lexique(s) remis en file.")


def _course_action(request, action):
    if action == "relancer":
        course = get_object_or_404(Course, pk=request.POST.get("item"), status=Course.Status.FAILED)
        coursegen.reset(course, request.user)
        messages.success(request, f"« {course.title} » remis en file.")
    elif action == "relancer-echecs":
        failed = list(Course.objects.filter(status=Course.Status.FAILED))
        for course in failed:
            coursegen.reset(course, request.user)
        messages.success(request, f"{len(failed)} cours remis en file.")



def _podcast_action(request, action):
    if action == "relancer":
        series = get_object_or_404(Series, pk=request.POST.get("item"), status=Series.Status.FAILED)
        podcastgen.retry(series, request.user)
        messages.success(request, f"« {series.title} » remise en file.")
    elif action == "relancer-echecs":
        failed = list(Series.objects.filter(status=Series.Status.FAILED))
        for series in failed:
            podcastgen.retry(series, request.user)
        messages.success(request, f"{len(failed)} série(s) de podcasts remise(s) en file.")


@content_manager_required
def generation_dashboard(request):
    """Avancement des générations en arrière-plan, avec pause et reprise."""
    services = [
        {
            "kind": "qcm",
            "icon": "🧠",
            "title": "QCM",
            "service": "quizgen",
            "unit": "guides avec QCM",
            "s": generation.quiz_stats(),
        },
        {
            "kind": "cours",
            "icon": "🎓",
            "title": "Cours",
            "service": "coursegen",
            "unit": "cours publiés",
            "s": course_stats(),
        },
        {
            "kind": "podcast",
            "icon": "🎙️",
            "title": "Podcasts",
            "service": "podcastgen",
            "unit": "séries publiées",
            "s": podcast_stats(),
        },
        {
            "kind": "lexique",
            "icon": "📖",
            "title": "Lexiques",
            "service": "glossarygen",
            "unit": "guides avec lexique",
            "s": generation.glossary_stats(),
        },
        {
            "kind": "audio",
            "icon": "🎧",
            "title": "Audio",
            "service": "narrator",
            "unit": "guides en audio",
            "s": generation.audio_stats(),
        },
    ]
    sequence = generation.order()  # ordre de passage : une génération à la fois
    services.sort(key=lambda item: sequence.index(item["kind"]))
    labels = {item["kind"]: f"{item['icon']} {item['title']}" for item in services}
    for item in services:
        s = item["s"]
        s["missing"] = batches.missing(
            item["kind"]
        )  # même décompte que les boutons de mise en file
        item["plural"] = batches.KINDS[item["kind"]][0]
        s["percent"] = round(100 * s["covered"] / s["eligible"]) if s["eligible"] else 0
        holder = generation.holder(item["kind"]) if s["pending"] and not s["running"] else None
        s["holder"] = labels.get(holder, "")
        # État affiché : pause demandée, mode jeu, service muet, au travail, en attente de son tour,
        # au repos.
        s["state"] = (
            "pause"
            if s["control"].paused
            else (
                "jeu"
                if s["gaming"]
                else (
                    "arret"
                    if not s["alive"]
                    else "marche" if s["running"] else "attente" if holder else "repos"
                )
            )
        )
    last_command = ServiceCommand.objects.filter(finished_at__isnull=False).first()
    return render(request, "catalogue/generation.html", {
        "services": services, "paused_count": generation.paused_count(),
        "stopped_count": sum(item["s"]["state"] == "arret" for item in services),
        "agent_alive": generation.agent_alive(), "command": generation.current_command(),
        "last_command": last_command, "can_control": request.user.is_superuser,
        "background_count": len(generation.BACKGROUND), "model": quizgen.model_name(),
        "gaming": generation.control(generation.Kind.GAMING),
        "audio_users_first": generation.audio_users_first(),
        "prereview_on": prereview.enabled(), "prereview_allowed": settings.PREREVIEW_ENABLED,
    })


@content_manager_required
@require_POST
def generation_action(request, kind):
    """Pause, reprise, mise en file de tout ce qui manque, nouvel essai après un échec."""
    action = request.POST.get("action")
    if kind == "services":
        # Agit sur la machine (conteneurs) : réservé aux superutilisateurs.
        if not request.user.is_superuser:
            raise PermissionDenied("Réservé aux administrateurs.")
        if action not in ServiceCommand.Action.values:
            raise Http404("Action inconnue.")
        if not generation.agent_alive():
            messages.error(
                request,
                "L'agent du poste ne répond pas : lancez « .\\dev up » (ou « .\\dev agent ») sur"
                " le PC.",
            )
        else:
            command = generation.request_services(action, request.user)
            messages.success(
                request,
                f"{command.get_action_display()} : demande transmise à l'agent du poste, "
                "la page se met à jour toute seule.",
            )
        return redirect("catalogue:generation_dashboard")
    if kind == "ordre" and action in ("utilisateurs-oui", "utilisateurs-non"):
        generation.set_audio_users_first(action == "utilisateurs-oui")
        messages.success(
            request,
            (
                "Les versions audio demandées par un utilisateur passent devant tout."
                if action == "utilisateurs-oui"
                else (
                    "Les versions audio demandées par un utilisateur suivent l'ordre de "
                    "passage, comme le reste."
                )
            ),
        )
        return redirect(reverse("catalogue:generation_dashboard") + "#ordre")
    if kind == generation.Kind.PREREVIEW and action in ("activer", "desactiver"):
        prereview.set_enabled(action == "activer", request.user)
        messages.success(
            request,
            (
                "Pré-relecteur IA activé : les prochains QCM et cours générés seront contrôlés"
                " avant la relecture."
                if action == "activer"
                else (
                    "Pré-relecteur IA désactivé : les "
                    "contenus générés passent directement en relecture, comme avant."
                )
            ),
        )
        return redirect(reverse("catalogue:generation_dashboard") + "#ordre")
    if (
        kind == "ordre"
        and request.POST.get("item") in generation.BACKGROUND
        and action in ("monter", "descendre")
    ):
        generation.move_in_order(request.POST["item"], -1 if action == "monter" else 1)
        messages.success(
            request,
            "Ordre de passage enregistré : il s'applique dès le prochain élément à générer.",
        )
        return redirect(reverse("catalogue:generation_dashboard") + "#ordre")
    if kind == "toutes" and action in ("pause", "reprendre"):
        generation.set_all_paused(action == "pause", request.user)
        messages.success(
            request,
            (
                "Toutes les générations sont en pause : celles en cours s'arrêtent à la prochaine "
                "étape, le travail déjà fait est conservé."
                if action == "pause"
                else "Toutes les générations reprennent."
            ),
        )
        return redirect("catalogue:generation_dashboard")
    if kind not in generation.Kind.values:
        raise Http404("Génération inconnue.")
    if action == "file" and kind in batches.KINDS:
        limit = None if request.POST.get("nombre") == "tout" else batches.BATCH
        queued = batches.queue(kind, limit, request.user)
        plural, singular = batches.KINDS[kind]
        messages.success(
            request, f"{queued} {plural if queued > 1 else singular} mis en file de génération."
        )
        target = request.POST.get("next", "")
        if target and url_has_allowed_host_and_scheme(
            target, {request.get_host()}, request.is_secure()
        ):
            return redirect(target)
        return redirect("catalogue:generation_dashboard")
    label = generation.Kind(kind).label
    if kind == generation.Kind.GAMING:
        if action in ("pause", "reprendre"):
            generation.set_gaming(action == "pause", request.user)
            messages.success(
                request,
                (
                    "Mode jeu activé : générations en pause, assistant coupé, modèle d'IA "
                    "déchargé de la carte graphique."
                    if action == "pause"
                    else "Mode jeu désactivé : l'IA et les générations reprennent."
                ),
            )
        target = request.POST.get("next", "")
        if target and url_has_allowed_host_and_scheme(
            target, {request.get_host()}, request.is_secure()
        ):
            return redirect(target)
        return redirect("catalogue:generation_dashboard")
    if action in ("pause", "reprendre"):
        generation.set_paused(kind, action == "pause", request.user)
        messages.success(
            request,
            (
                f"{label} : en pause. Les générations en cours s'arrêtent à la prochaine étape, "
                "le travail déjà fait est conservé."
                if action == "pause"
                else f"{label} : reprise."
            ),
        )
    elif kind == generation.Kind.COURSE:
        _course_action(request, action)
    elif kind == generation.Kind.PODCAST:
        _podcast_action(request, action)
    elif kind == generation.Kind.GLOSSARY:
        _glossary_action(request, action)
    elif action == "relancer-echecs" and kind == generation.Kind.QUIZ:
        guides = Guide.objects.filter(pk__in=generation.retryable_quiz_failures().values("guide"))
        for guide in guides:
            quizgen.request(guide, request.user)
        messages.success(request, f"{len(guides)} guide(s) remis en file de génération des QCM.")
    elif action == "relancer-echecs":
        failed = list(
            Narration.objects.filter(status=Narration.Status.FAILED).select_related("guide")
        )
        for item in failed:
            try:
                narration.request(item.guide, request.user, priority=narration.BULK_PRIORITY)
            except narration.NarrationError:
                pass  # PDF retiré depuis
        messages.success(request, f"{len(failed)} version(s) audio remise(s) en file.")
    elif action == "relancer":
        guide = get_object_or_404(Guide, pk=request.POST.get("guide"))
        try:
            (quizgen.request(guide, request.user) if kind == generation.Kind.QUIZ
             else narration.request(guide, request.user, priority=narration.BULK_PRIORITY))
        except (narration.NarrationError, quizgen.GenerationError) as exc:
            messages.error(request, f"« {guide.title} » : {exc}.")
        else:
            messages.success(request, f"« {guide.title} » remis en file.")
    return redirect("catalogue:generation_dashboard")


# --- Files d'attente : ordre et retrait --------------------------------------------------------
QUEUE_TITLES = {
    "qcm": "🧠 QCM",
    "cours": "🎓 Cours",
    "podcast": "🎙️ Podcasts",
    "lexique": "📖 Lexiques",
    "audio": "🎧 Versions audio",
}


@content_manager_required
def generation_queue(request, kind):
    """File d'attente d'une génération, dans l'ordre où elle sera traitée : déplacer ou retirer."""
    if kind not in QUEUE_TITLES:
        raise Http404("File inconnue.")
    return render(
        request,
        "catalogue/generation_queue.html",
        {
            "kind": kind,
            "title": QUEUE_TITLES[kind],
            "rows": queues.rows(kind),
            "titles": QUEUE_TITLES.items(),
        },
    )


@content_manager_required
@require_POST
def generation_queue_action(request, kind):
    if kind not in QUEUE_TITLES:
        raise Http404("File inconnue.")
    action = request.POST.get("action")
    url = reverse("catalogue:generation_queue", args=[kind])
    try:
        pk = int(request.POST.get("item", ""))
    except ValueError:
        pk = None
    if action in queues.MOVES and pk:
        position = queues.move(kind, pk, action)
        if position is None:
            messages.info(
                request,
                "Cet élément n'est plus en attente (il est peut-être en cours de génération).",
            )
            return redirect(url)
        return redirect(f"{url}#e{pk}")
    if action == "retirer" and pk:
        title = queues.remove(kind, pk)
        if title is None:
            messages.info(
                request,
                "Cet élément n'est plus en attente (il est peut-être en cours de génération).",
            )
        else:
            messages.success(request, f"« {title} » retiré de la file.")
        return redirect(url)
    if action == "vider":
        count = queues.clear(kind)
        messages.success(request, f"{count} élément(s) retiré(s) de la file.")
        return redirect(url)
    messages.error(request, "Action inconnue.")
    return redirect(url)


# --- Lexiques : relecture, et écriture dans l'atelier de création
# -----------------------------------
GLOSSARY_MIN_ENTRIES = 5


def _glossary_access(request, item):
    """Un rédacteur n'ouvre que ses propres lexiques ; un gestionnaire, tous."""
    user = request.user
    if not (user.is_content_manager or item.author_id == user.pk):
        raise PermissionDenied("Ce lexique appartient à un autre rédacteur.")


def _glossary_editable(user, item):
    """Le rédacteur, son brouillon ; le gestionnaire, aussi un lexique à relire ou déjà publié
    (effet immédiat)."""
    if item.status == Glossary.Status.WRITING:
        return user.is_content_manager or item.author_id == user.pk
    return user.is_content_manager and item.status in (
        Glossary.Status.REVIEW,
        Glossary.Status.PUBLISHED,
    )


def _glossary_hint(user, item):
    count = item.entries.count()
    if item.status == Glossary.Status.WRITING:
        if user.is_content_manager:
            return "Brouillon : publiez quand c'est prêt."
        missing = GLOSSARY_MIN_ENTRIES - count
        return "Brouillon : soumettez quand c'est prêt, un gestionnaire relira et publiera." + \
            (f" À faire avant : encore {missing} entrée(s) au moins." if missing > 0 else "")
    if item.status == Glossary.Status.REVIEW:
        return (
            "À relire : vérifiez les formes développées et les définitions, supprimez les mots trop"
            " courants, puis publiez."
            if user.is_content_manager
            else "En relecture : plus modifiable, sauf s'il vous est renvoyé."
        )
    if item.status in (Glossary.Status.PENDING, Glossary.Status.GENERATING):
        return "L'IA relève les sigles et termes à son tour (générations en arrière-plan)."
    if item.status == Glossary.Status.PUBLISHED:
        return (
            "Publié : vos ajouts et corrections s'affichent tout de suite pendant la lecture."
            if user.is_content_manager
            else "Publié : un gestionnaire de contenu peut le compléter."
        )
    return ""


def _glossary_back(user, item):
    if item.author_id or not user.is_content_manager:
        from studio.sources import list_url  # import local : studio importe catalogue
        return list_url("lexique", item.status)
    return reverse("catalogue:review_list") + "?type=lexique"


@author_required
def glossary_review(request, pk):
    """Un lexique : toutes les entrées, modifiables, sur une page (relecture ou écriture par un
    rédacteur)."""
    item = get_object_or_404(Glossary.objects.select_related("guide", "author"), pk=pk)
    _glossary_access(request, item)
    return render(
        request,
        "catalogue/glossary_review.html",
        {
            "glossary": item,
            "entries": list(item.entries.all()),
            "editable": _glossary_editable(request.user, item),
            "kinds": GlossaryEntry.Kind.choices,
            "writing": item.status == Glossary.Status.WRITING,
            "manager": request.user.is_content_manager,
            "back_url": _glossary_back(request.user, item),
            "back_label": (
                "Lexiques" if item.author_id or not request.user.is_content_manager else "Relecture"
            ),
            "hint": _glossary_hint(request.user, item),
            "min_entries": GLOSSARY_MIN_ENTRIES,
        },
    )


def _glossary_url(item, anchor=""):
    return reverse("catalogue:glossary_review", args=[item.pk]) + anchor


@author_required
@require_POST
def glossary_review_action(request, pk):
    item = get_object_or_404(Glossary.objects.select_related("guide"), pk=pk)
    _glossary_access(request, item)
    action, user = request.POST.get("action"), request.user
    back = _glossary_back(user, item)
    if item.status == Glossary.Status.WRITING and _glossary_editable(user, item):
        if action == "soumettre":
            if item.entries.count() < GLOSSARY_MIN_ENTRIES:
                messages.error(
                    request, f"Avant de soumettre : au moins {GLOSSARY_MIN_ENTRIES} entrées."
                )
                return redirect(_glossary_url(item))
            item.status = Glossary.Status.REVIEW
            item.save(update_fields=["status"])
            messages.success(request, "Lexique soumis à la relecture d'un gestionnaire de contenu.")
            return redirect(back)
        if action == "generer":
            glossary.reset(
                item, user
            )  # l'auteur est gardé : le lexique revient ensuite dans l'atelier
            messages.success(
                request,
                "L'IA relèvera les sigles et termes du guide à son tour (générations en "
                "arrière-plan) ; le lexique reviendra ensuite ici, en rédaction.",
            )
            return redirect(back)
        if action == "supprimer":
            title = item.guide.title
            item.delete()
            messages.info(request, f"Lexique de « {title} » supprimé.")
            return redirect("studio:list", "lexique")
    if (
        action == "renvoyer"
        and user.is_content_manager
        and item.status == Glossary.Status.REVIEW
        and item.author_id
    ):
        item.status = Glossary.Status.WRITING
        item.save(update_fields=["status"])
        messages.info(request, f"Lexique de « {item.guide.title} » renvoyé à son rédacteur.")
        return redirect(back)
    if action != "ajouter" and not user.is_content_manager:
        messages.error(request, "Action impossible en ce moment.")
        return redirect(_glossary_url(item))
    if action == "relancer" and item.status in (
        Glossary.Status.FAILED,
        Glossary.Status.REVIEW,
        Glossary.Status.DISCARDED,
    ):
        glossary.reset(item, request.user)
        messages.success(request, "Lexique remis en file : il sera entièrement régénéré.")
        return redirect(reverse("catalogue:review_list") + "?type=lexique")
    if not _glossary_editable(user, item):
        messages.error(request, "Ce lexique n'est pas modifiable en ce moment.")
        return redirect(back)
    if action == "publier" and user.is_content_manager:
        try:
            glossary.publish(item, request.user)
        except glossary.GenerationError as exc:
            messages.error(request, str(exc))
            return redirect(_glossary_url(item))
        messages.success(
            request, f"Lexique de « {item.guide.title} » publié : il s'affiche pendant la lecture."
        )
        return redirect(reverse("catalogue:review_list") + "?type=lexique")
    if action == "abandonner":
        item.status = Glossary.Status.DISCARDED
        item.save(update_fields=["status"])
        messages.info(request, "Lexique abandonné.")
        return redirect(reverse("catalogue:review_list") + "?type=lexique")
    if action == "ajouter":
        entry = glossary.check_entry(
            {
                "terme": request.POST.get("term", ""),
                "definition": request.POST.get("definition", ""),
                "forme_developpee": request.POST.get("expansion", ""),
            },
            request.POST.get("term", ""),
        )  # ajout manuel : pas de contrôle de présence
        if entry is None or item.entries.filter(key=entry["key"]).exists():
            messages.error(
                request,
                "Il faut un terme (2 à 60 caractères), absent du lexique, et une définition "
                "d'au moins 20 caractères.",
            )
        else:
            GlossaryEntry.objects.create(glossary=item, **entry)
            messages.success(request, f"« {entry['term']} » ajouté.")
        return redirect(_glossary_url(item, "#ajouter"))
    messages.error(request, "Action inconnue.")
    return redirect(_glossary_url(item))


@author_required
@require_POST
def glossary_entry(request, pk):
    entry = get_object_or_404(GlossaryEntry.objects.select_related("glossary"), pk=pk)
    item = entry.glossary
    _glossary_access(request, item)
    if not _glossary_editable(request.user, item):
        messages.error(request, "Ce lexique n'est pas modifiable en ce moment.")
    elif request.POST.get("action") == "supprimer":
        entry.delete()
        messages.info(request, f"« {entry.term} » supprimé.")
    else:
        term = request.POST.get("term", "").strip()
        definition = request.POST.get("definition", "").strip()
        kind = request.POST.get("kind")
        if (
            not 2 <= len(term) <= 60
            or len(definition) < 20
            or kind not in GlossaryEntry.Kind.values
        ):
            messages.error(
                request,
                "Il faut un terme (2 à 60 caractères) et une définition d'au moins 20 caractères.",
            )
        else:
            entry.term, entry.kind, entry.definition = term, kind, definition[:400]
            entry.expansion = request.POST.get("expansion", "").strip()[:200]
            entry.key = glossary.entry_key(term, kind)
            if item.entries.filter(key=entry.key).exclude(pk=entry.pk).exists():
                messages.error(request, f"« {term} » figure déjà dans le lexique.")
            else:
                entry.save()
                messages.success(request, f"« {term} » enregistré.")
    return redirect(_glossary_url(item, f"#e{entry.pk}"))
