"""
Écriture et relecture des cours (générés par l'IA ou écrits dans l'atelier de création).

Une leçon par page (texte, points à retenir, questions du quiz), puis l'examen final ; tout est
modifiable avant la publication. Le rédacteur modifie ses propres cours « en rédaction » puis les
soumet ; le gestionnaire de contenu relit, renvoie au rédacteur ou publie.
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from catalogue import prereview
from catalogue.review_views import content_manager_required
from studio import ai, sources
from studio.permissions import author_required, can_edit, check_view, is_mine

from . import coursegen
from .models import Course, CourseQuestion, Lesson

Status = Course.Status


@content_manager_required
def review_list(request):
    """Ancienne liste des cours : la relecture est rassemblée dans une même fenêtre (onglet
    Cours)."""
    return redirect(reverse("catalogue:review_list") + "?type=cours")


def _back_url(user, course):
    """Après une action de fin (soumettre, publier…) : la liste de l'atelier pour un rédacteur, la
    relecture sinon."""
    if course.author_id or not user.is_content_manager:
        return sources.list_url("cours", course.status)
    return reverse("catalogue:review_list") + "?type=cours"


def _hint(user, course, problems):
    todo = f" À faire avant : {' ; '.join(problems)}." if problems else ""
    if course.status == Status.WRITING:
        return (
            "Brouillon : publiez quand c'est prêt."
            if user.is_content_manager
            else "Brouillon : soumettez quand c'est prêt, un gestionnaire relira et publiera."
        ) + todo
    if course.status == Status.REVIEW:
        return (
            "À relire : publiez"
            + (", ou renvoyez au rédacteur." if course.author_id else ".")
            + todo
            if user.is_content_manager
            else "En relecture : plus modifiable, sauf s'il vous est renvoyé."
        )
    if course.status in (Status.PENDING, Status.GENERATING):
        return "L'IA écrit le cours à son tour (générations en arrière-plan)."
    if course.status == Status.PUBLISHED:
        return "Publié : vos modifications sont visibles tout de suite par les apprenants."
    return ""


@author_required
def review_course(request, pk):
    """Un cours : présentation, puis une leçon par page, puis l'examen (`?etape=`)."""
    course = get_object_or_404(Course.objects.select_related("category", "guide", "author"), pk=pk)
    check_view(request.user, course)
    lessons = list(course.lessons.annotate(question_count=Count("questions")))
    exam_questions = list(course.questions.filter(lesson__isnull=True))
    step = request.GET.get("etape", "1" if lessons else "examen")
    lesson = next((l for l in lessons if str(l.position) == step), None)
    if lesson is None and step != "examen":
        step = "examen"
    questions = list(lesson.questions.all()) if lesson else exam_questions
    positions = [str(l.position) for l in lessons] + ["examen"]
    index = positions.index(step)
    user = request.user
    return render(
        request,
        "courses/review_course.html",
        {
            "course": course,
            "lessons": lessons,
            "lesson": lesson,
            "questions": questions,
            "step": step,
            "previous": positions[index - 1] if index else None,
            "next": positions[index + 1] if index + 1 < len(positions) else None,
            "exam_count": len(exam_questions),
            "problems": coursegen.publish_problems(course),
            "key_points_text": "\n".join(lesson.key_points) if lesson else "",
            "editable": can_edit(user, course),
            "min_lesson": coursegen.MIN_LESSON_QUESTIONS,
            "writing": course.status == Status.WRITING,
            "mine": is_mine(user, course),
            "manager": user.is_content_manager,
            "back_url": _back_url(user, course),
            "back_label": (
                "Cours" if course.author_id or not user.is_content_manager else "Relecture"
            ),
            "hint": _hint(
                user, course, coursegen.publish_problems(course) if can_edit(user, course) else []
            ),
            "ai_actions": [
                (key, label) for key, (label, _i, _c) in ai.ACTIONS.items() if key != "dialogue"
            ],
            "ai_busy": ai.available(),
            "prereview_notes": (
                prereview.course_notes(course, step) + prereview.course_notes(course, "")
            ),
            "prereview_steps": {n["step"] for n in (course.prereview or {}).get("notes", [])},
        },
    )


def _step_url(course, step):
    return reverse("courses:review_course", args=[course.pk]) + f"?etape={step}"


@author_required
@require_POST
def review_course_action(request, pk):
    course = get_object_or_404(Course, pk=pk)
    user = request.user
    check_view(user, course)
    action = request.POST.get("action")
    back = _back_url(user, course)
    manager = user.is_content_manager
    if action == "relancer" and (
        manager
        and course.status in (Status.FAILED, Status.REVIEW, Status.DISCARDED)
        or can_edit(user, course)
        and course.author_id
        and course.status != Status.PUBLISHED
    ):
        coursegen.reset(course, user)  # un cours de rédacteur garde son auteur et ses consignes
        messages.success(request, "Cours remis en file : l'IA le réécrira entièrement" +
                         (", puis il reviendra dans l'atelier." if course.author_id else "."))
        return redirect(back)
    if action == "renvoyer" and manager and course.status == Status.REVIEW and course.author_id:
        course.status = Status.WRITING
        course.save(update_fields=["status"])
        messages.info(
            request, f"« {course.title} » est renvoyé à {course.author.username}, en rédaction."
        )
        return redirect(back)
    if not can_edit(user, course):
        messages.error(request, "Ce cours n'est pas modifiable en ce moment.")
        return redirect(back)
    if action == "enregistrer":
        title = request.POST.get("title", "").strip()
        if len(title) < 5:
            messages.error(request, "Le titre est trop court.")
        else:
            course.title, course.summary = title[:200], request.POST.get("summary", "").strip()
            course.brief = request.POST.get("brief", course.brief).strip()[:3000]
            course.save(update_fields=["title", "summary", "brief"])
            messages.success(request, "Présentation du cours enregistrée.")
        return redirect(_step_url(course, request.POST.get("etape", "1")))
    if action == "ajouter-lecon":
        position = (course.lessons.aggregate(n=Max("position"))["n"] or 0) + 1
        Lesson.objects.create(course=course, position=position, title=f"Leçon {position}", body="")
        messages.success(
            request,
            f"Leçon {position} ajoutée : donnez-lui un titre, puis écrivez-la (ou demandez un "
            "brouillon à l'IA).",
        )
        return redirect(_step_url(course, position))
    if action == "soumettre" and course.status == Status.WRITING:
        problems = coursegen.publish_problems(course)
        if problems:
            messages.error(request, "Avant de soumettre : " + " ; ".join(problems) + ".")
            return redirect(_step_url(course, request.POST.get("etape", "1")))
        course.status = Status.REVIEW
        course.save(update_fields=["status"])
        messages.success(
            request,
            f"« {course.title} » est soumis à la relecture : un gestionnaire de contenu le "
            "publiera ou vous le renverra avec ses remarques.",
        )
        return redirect(back)
    if action == "publier" and manager:
        try:
            coursegen.publish(course, user)
        except coursegen.GenerationError as exc:
            messages.error(request, str(exc))
            return redirect(_step_url(course, "examen"))
        messages.success(request, f"« {course.title} » est publié : il est visible des apprenants.")
        return redirect(back)
    if action == "abandonner" and manager and course.status == Status.REVIEW:
        course.status = Status.DISCARDED
        course.save(update_fields=["status"])
        messages.info(request, "Cours abandonné.")
        return redirect(back)
    if (
        action == "supprimer"
        and course.status == Status.WRITING
        and (is_mine(user, course) or manager)
    ):
        title = course.title
        course.delete()
        messages.info(request, f"« {title} » supprimé.")
        return redirect("studio:list", "cours")
    messages.error(request, "Action inconnue.")
    return redirect(_step_url(course, "1"))


@author_required
@require_POST
def review_lesson(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("course"), pk=pk)
    course = lesson.course
    check_view(request.user, course)
    if not can_edit(request.user, course):
        messages.error(request, "Ce cours n'est pas modifiable en ce moment.")
        return redirect(_step_url(course, lesson.position))
    if request.POST.get("action") == "supprimer":
        with transaction.atomic():
            lesson.delete()
            for n, item in enumerate(
                course.lessons.order_by("position"), start=1
            ):  # positions sans trou
                if item.position != n:
                    Lesson.objects.filter(pk=item.pk).update(position=n)
        messages.info(request, f"Leçon « {lesson.title} » supprimée.")
        return redirect(_step_url(course, max(lesson.position - 1, 1)) if course.lessons.exists()
                        else _step_url(course, "examen"))
    title, body = request.POST.get("title", "").strip(), request.POST.get("body", "").strip()
    if len(title) < 3 or len(body) < 100:
        messages.error(request, "Il faut un titre et un texte d'au moins 100 caractères.")
    else:
        lesson.title, lesson.body = title[:200], body
        lesson.objective = request.POST.get("objective", "").strip()[:300]
        lesson.key_points = coursegen.clean_lines(request.POST.get("key_points", ""))[:8]
        lesson.minutes = max(1, round(len(body.split()) / 180))
        lesson.save()
        messages.success(request, f"Leçon {lesson.position} enregistrée.")
    return redirect(_step_url(course, lesson.position))


def _question_fields(request):
    """(énoncé, propositions — la bonne d'abord —, explication), ou None si le formulaire est
    incomplet."""
    text = request.POST.get("text", "").strip()
    choices = [request.POST.get("correct", "").strip()] + [
        request.POST.get(f"wrong_{i}", "").strip() for i in range(1, 5)
    ]
    choices = [c for c in choices if c]
    if len(text) < 10 or not request.POST.get("correct", "").strip() or len(choices) < 3 \
            or len({c.lower() for c in choices}) != len(choices):
        return None
    return (
        text[:500],
        [c[:300] for c in choices],
        request.POST.get("explanation", "").strip()[:1000],
    )


INCOMPLETE = (
    "Il faut un énoncé, une bonne réponse et au moins deux mauvaises réponses, toutes différentes."
)


@author_required
@require_POST
def review_question(request, pk):
    question = get_object_or_404(CourseQuestion.objects.select_related("course", "lesson"), pk=pk)
    course = question.course
    check_view(request.user, course)
    step = question.lesson.position if question.lesson_id else "examen"
    if not can_edit(request.user, course):
        messages.error(request, "Ce cours n'est pas modifiable en ce moment.")
    elif request.POST.get("action") == "supprimer":
        question.delete()
        messages.info(request, "Question supprimée.")
    else:
        fields = _question_fields(request)
        if fields is None:
            messages.error(request, INCOMPLETE)
        else:
            question.text, question.choices, question.explanation = fields
            question.answer = 0
            question.save()
            messages.success(request, "Question enregistrée.")
    return redirect(_step_url(course, step) + f"#q{question.pk}")


@author_required
@require_POST
def add_question(request, pk):
    """Nouvelle question écrite à la main, pour une leçon (`lecon` = position) ou l'examen final."""
    course = get_object_or_404(Course, pk=pk)
    check_view(request.user, course)
    step = request.POST.get("lecon", "examen")
    if not can_edit(request.user, course):
        messages.error(request, "Ce cours n'est pas modifiable en ce moment.")
        return redirect(_step_url(course, step))
    lesson = None if step == "examen" else get_object_or_404(Lesson, course=course, position=step)
    fields = _question_fields(request)
    if fields is None:
        messages.error(request, INCOMPLETE)
        return redirect(_step_url(course, step) + "#nouvelle-question")
    text, choices, explanation = fields
    position = (course.questions.filter(lesson=lesson).aggregate(n=Max("position"))["n"] or 0) + 1
    question = CourseQuestion.objects.create(
        course=course,
        lesson=lesson,
        position=position,
        text=text,
        choices=choices,
        answer=0,
        explanation=explanation,
    )
    messages.success(request, "Question ajoutée.")
    return redirect(_step_url(course, step) + f"#q{question.pk}")
