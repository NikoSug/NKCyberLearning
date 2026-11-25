"""
QCM d'un guide écrit dans l'atelier : les 6 séries de questions (de la plus facile à la plus
difficile),
écrites à la main ou proposées par l'IA série par série, puis relues comme les QCM générés.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.ratelimit import rate_limit
from catalogue import assistant, diagrams, narration_text, quizgen
from catalogue.models import QuizDraft, QuizDraftQuestion

from . import ai, sources
from .permissions import author_required

Status = QuizDraft.Status
WRONG_FIELDS = ("wrong_1", "wrong_2", "wrong_3", "wrong_4")


def _draft(request, pk):
    draft = get_object_or_404(QuizDraft.objects.select_related("guide", "author"), pk=pk)
    user = request.user
    if not (user.is_content_manager or draft.author_id == user.pk):
        raise PermissionDenied("Ce QCM appartient à un autre rédacteur.")
    return draft


def _editable(user, draft):
    return draft.status == Status.WRITING and (
        user.is_content_manager or draft.author_id == user.pk
    )


def _url(draft, series, anchor=""):
    return reverse("studio:qcm_edit", args=[draft.pk]) + f"?serie={series}{anchor}"


def _series_number(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return number if number in quizgen.SERIES else 1


def problems(draft):
    counts = dict(draft.questions.values_list("series").annotate(n=Count("pk")))
    missing = [str(n) for n in quizgen.SERIES if counts.get(n, 0) < quizgen.PER_SERIES]
    return (
        [
            f"au moins {quizgen.PER_SERIES} questions dans chaque série (à compléter :"
            f" série{'s' if len(missing) > 1 else ''} {', '.join(missing)})"
        ]
        if missing
        else []
    )


@author_required
def qcm_edit(request, pk):
    draft = _draft(request, pk)
    number = _series_number(request.GET.get("serie"))
    counts = dict(draft.questions.values_list("series").annotate(n=Count("pk")))
    return render(
        request,
        "studio/qcm_edit.html",
        {
            "draft": draft,
            "number": number,
            "level": quizgen.LEVELS[number],
            "series": [
                {
                    "number": n,
                    "count": counts.get(n, 0),
                    "complete": counts.get(n, 0) >= quizgen.PER_SERIES,
                }
                for n in quizgen.SERIES
            ],
            "questions": list(draft.questions.filter(series=number).order_by("position")),
            "editable": _editable(request.user, draft),
            "manager": request.user.is_content_manager,
            "problems": problems(draft),
            "per_series": quizgen.PER_SERIES,
            "ai_busy": ai.available(),
            "published_count": draft.guide.quiz_questions.count(),
            "hint": _hint(request.user, draft),
            "back_url": sources.list_url("qcm", draft.status),
        },
    )


def _hint(user, draft):
    found = problems(draft)
    todo = f" À faire avant : {' ; '.join(found)}." if found else ""
    if draft.status == Status.WRITING:
        return (
            "Brouillon : écrivez les 6 séries (ou faites-les compléter par l'IA), puis soumettez."
            + todo
        )
    if draft.status == Status.REVIEW:
        return ("Soumis : validez chaque question, puis publiez." if user.is_content_manager
                else "En relecture : plus modifiable, sauf s'il vous est renvoyé.")
    if draft.status in (Status.PENDING, Status.GENERATING):
        return "L'IA complète les séries à son tour ; le QCM reviendra ensuite en brouillon."
    return ""


def _fields(request):
    text = request.POST.get("text", "").strip()
    correct = request.POST.get("correct", "").strip()
    wrong = [request.POST.get(name, "").strip() for name in WRONG_FIELDS]
    choices = [correct] + [w for w in wrong if w]
    if (
        len(text) < 10
        or not correct
        or len(choices) < 3
        or len({c.lower() for c in choices}) != len(choices)
    ):
        return None
    return {"text": text[:500], "choices": [c[:300] for c in choices],
            "explanation": request.POST.get("explanation", "").strip()[:1000],
            "source": request.POST.get("source", "").strip()[:120],
            "diagram": request.POST.get("diagram", "").strip()}


INCOMPLETE = (
    "Il faut un énoncé, une bonne réponse et au moins deux mauvaises réponses, toutes différentes."
)


@author_required
@require_POST
def qcm_question(request, pk):
    """Ajouter, modifier ou supprimer une question d'une série."""
    draft = _draft(request, pk)
    number = _series_number(request.POST.get("serie"))
    if not _editable(request.user, draft):
        messages.error(request, "Ce QCM n'est pas modifiable en ce moment.")
        return redirect(_url(draft, number))
    verb = request.POST.get("action")
    question = None
    if request.POST.get("question"):
        question = get_object_or_404(QuizDraftQuestion, pk=request.POST["question"], draft=draft)
    if verb == "supprimer" and question:
        question.delete()
        messages.info(request, "Question supprimée.")
        return redirect(_url(draft, number))
    fields = _fields(request)
    if fields is None:
        messages.error(request, INCOMPLETE)
        return redirect(
            _url(draft, number, f"#q{question.pk}" if question else "#nouvelle-question")
        )
    diagram_problem = diagrams.problem(fields["diagram"])
    if diagram_problem:
        messages.error(request, f"Schéma non enregistré : {diagram_problem}.")
        return redirect(
            _url(draft, number, f"#q{question.pk}" if question else "#nouvelle-question")
        )
    if question:
        for name, value in fields.items():
            setattr(question, name, value)
        question.edited = True
        question.save()
    else:
        position = (
            draft.questions.filter(series=number).aggregate(n=Max("position"))["n"] or 0
        ) + 1
        question = QuizDraftQuestion.objects.create(
            draft=draft, series=number, position=position, **fields
        )
    messages.success(request, "Question enregistrée.")
    return redirect(_url(draft, number, f"#q{question.pk}"))


@author_required
@require_POST
@rate_limit("ia-atelier", 60, 3600)
def qcm_ai(request, pk):
    """Questions de la série proposées par l'IA, à partir de la partie du guide qui lui
    correspond."""
    draft = _draft(request, pk)
    number = _series_number(request.POST.get("serie"))
    if not _editable(request.user, draft):
        messages.error(request, "Ce QCM n'est pas modifiable en ce moment.")
        return redirect(_url(draft, number))
    reason = ai.available()
    if reason:
        messages.error(request, reason)
        return redirect(_url(draft, number))
    try:
        sections = [
            narration_text.Section(s.title, s.text) for s in assistant.sections(draft.guide)
        ]
        part = quizgen.split_parts(sections)[number - 1]
        existing = list(draft.questions.values_list("text", flat=True))
        needed = max(3, quizgen.PER_SERIES - draft.questions.filter(series=number).count())
        found = quizgen.generate_series(part, number, needed, existing)
    except (assistant.AssistantError, quizgen.GenerationError, IndexError) as exc:
        messages.error(request, f"L'IA n'a pas pu proposer de questions : {exc}")
        return redirect(_url(draft, number))
    checks = quizgen.cross_check(part, found)
    position = draft.questions.filter(series=number).aggregate(n=Max("position"))["n"] or 0
    for offset, (item, check) in enumerate(zip(found, checks), start=1):
        QuizDraftQuestion.objects.create(draft=draft, series=number, position=position + offset,
                                         context_title=part.title, cross_check=check, **item)
    messages.success(
        request,
        f"{len(found)} question(s) proposée(s) par l'IA pour la série {number} : relisez-les "
        "(celles qu'elle n'a pas su vérifier sont signalées).",
    )
    return redirect(_url(draft, number))


@author_required
@require_POST
def qcm_action(request, pk):
    """Soumettre, compléter par l'IA (en arrière-plan), renvoyer, supprimer."""
    draft = _draft(request, pk)
    user, verb = request.user, request.POST.get("action")
    back = _url(draft, _series_number(request.POST.get("serie")))
    if (
        verb == "renvoyer"
        and user.is_content_manager
        and draft.status == Status.REVIEW
        and draft.author_id
    ):
        draft.status = Status.WRITING
        draft.save(update_fields=["status"])
        messages.info(request, f"QCM de « {draft.guide.title} » renvoyé à son rédacteur.")
        return redirect(reverse("catalogue:review_list") + "?type=qcm")
    if not _editable(user, draft):
        messages.error(request, "Ce QCM n'est pas modifiable en ce moment.")
        return redirect(back)
    if verb == "soumettre":
        found = problems(draft)
        if found:
            messages.error(request, "Avant de soumettre : " + " ; ".join(found) + ".")
            return redirect(back)
        draft.status = Status.REVIEW
        draft.save(update_fields=["status"])
        messages.success(
            request,
            "QCM soumis à la relecture : un gestionnaire validera chaque question puis le"
            " publiera.",
        )
        return redirect("studio:list", "qcm")
    if verb == "completer":
        draft.status, draft.error = Status.PENDING, ""
        draft.save(update_fields=["status", "error"])
        messages.success(
            request,
            "Les séries incomplètes seront complétées par l'IA à son tour (générations en "
            "arrière-plan) ; le QCM reviendra ensuite ici, en rédaction.",
        )
        return redirect("studio:list", "qcm")
    if verb == "supprimer":
        title = draft.guide.title
        draft.delete()
        messages.info(request, f"QCM de « {title} » supprimé.")
        return redirect("studio:list", "qcm")
    messages.error(request, "Action inconnue.")
    return redirect(back)
