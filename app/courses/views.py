import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalogue import glossary, quiz
from catalogue.models import Guide, Level

from . import learning
from .models import Course, CourseQuestion, ExamAttempt


def _course(slug):
    return get_object_or_404(learning.published().select_related("category", "guide"), slug=slug)


def _lesson(user, course, position):
    """Leçon ouverte pour l'utilisateur, ou None (verrouillée) ; avec la liste des leçons et leurs
    états."""
    lessons = learning.lessons_with_state(user, course)
    lesson = next((l for l in lessons if l.position == position), None)
    return lesson, lessons


def lesson_guides(course, lesson):
    """Guides dont le lexique accompagne une leçon : celui du cours, ou ceux cités en source (cours
    thématique)."""
    if course.guide_id:
        return [course.guide]
    guides = (
        Guide.objects.filter(category=course.category)
        if course.category_id
        else Guide.objects.none()
    )
    return [g for g in guides if g.title in lesson.sources] or list(guides)


@login_required
def course_list(request):
    query = request.GET.get("q", "").strip()
    level = request.GET.get("niveau", "")
    courses = learning.published().select_related("category", "guide").annotate(
        lesson_count=Count("lessons", distinct=True))
    guides = courses.filter(kind=Course.Kind.GUIDE)
    if query:
        guides = guides.filter(Q(title__icontains=query) | Q(guide__title__icontains=query))
    if level in Level.values:
        guides = guides.filter(level=level)
    themes = list(courses.filter(kind=Course.Kind.THEME).order_by("category__position", "title"))
    guides = list(guides.order_by("level", "title"))
    passed = learning.course_progress(request.user, themes + guides)
    for course in themes + guides:
        course.passed = passed.get(course.pk, 0)
    return render(request, "courses/course_list.html", {
        "themes": themes, "guides": guides, "query": query, "level": level, "levels": Level.choices,
        "in_progress": [c for c in themes + guides if 0 < c.passed < c.lesson_count][:4],
    })


@login_required
def course_detail(request, slug):
    course = _course(slug)
    lessons = learning.lessons_with_state(request.user, course)
    return render(request, "courses/course_detail.html", {
        "course": course, "lessons": lessons, "exam_open": learning.exam_open(lessons),
        "exam_count": course.questions.filter(lesson__isnull=True).count(),
        "best_exam": learning.best_exam(request.user, course),
        "next_lesson": next((l for l in lessons if l.state == learning.OPEN), None),
        "guide": course.guide,  # assistant de lecture : s'appuie sur le guide d'un cours de guide
        "pass": learning.PASS_LABELS,
    })


@login_required
def lesson(request, slug, position):
    course = _course(slug)
    current, lessons = _lesson(request.user, course, position)
    if current is None:
        raise Http404("Leçon introuvable.")
    if current.state == learning.LOCKED:
        messages.info(request, "Réussissez d'abord le quiz de la leçon précédente.")
        return redirect(course.get_absolute_url())
    return render(
        request,
        "courses/lesson.html",
        {
            "course": course,
            "lesson": current,
            "lessons": lessons,
            "guide": course.guide,
            "glossary_entries": glossary.published_entries(lesson_guides(course, current)),
            "glossary_scope": "cours",
            "question_count": current.questions.count(),
            "pass": learning.PASS_LABELS,
        },
    )


@login_required
def lesson_quiz(request, slug, position):
    """Quiz d'une leçon : une question à la fois, correction immédiate (sans JavaScript : envoi
    groupé)."""
    course = _course(slug)
    current, _lessons = _lesson(request.user, course, position)
    if current is None or current.state == learning.LOCKED:
        return redirect(course.get_absolute_url())
    questions = list(current.questions.all())
    if request.method == "POST":
        score, total, passed, gain, answers = learning.finish_quiz(
            request, current, questions, request.POST
        )
        for question in questions:
            question.given = answers.get(str(question.pk))
        next_lesson = course.lessons.filter(position=position + 1).first()
        return render(
            request,
            "courses/lesson_result.html",
            {
                "course": course,
                "lesson": current,
                "questions": questions,
                "score": score,
                "total": total,
                "percent": round(100 * score / total) if total else 0,
                "passed": passed,
                "gain": gain,
                "next_lesson": next_lesson if passed else None,
                "pass": learning.PASS_LABELS,
            },
        )
    learning.start_quiz(request, current)
    return render(request, "courses/lesson_quiz.html", {
        "course": course, "lesson": current, "questions": questions, "no_assistant": True,
        "pass": learning.PASS_LABELS,
    })


@login_required
@require_POST
def lesson_answer(request, slug, position):
    """Correction immédiate d'une réponse (la première réponse donnée est retenue)."""
    course = _course(slug)
    current, _lessons = _lesson(request.user, course, position)
    if current is None or current.state == learning.LOCKED:
        return JsonResponse({"error": "Leçon verrouillée."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
        question = CourseQuestion.objects.get(pk=int(payload.get("question")), lesson=current)
        choice = int(payload.get("choice"))
    except (ValueError, TypeError, CourseQuestion.DoesNotExist):
        return JsonResponse({"error": "Réponse invalide."}, status=400)
    if not 0 <= choice < len(question.choices):
        return JsonResponse({"error": "Réponse invalide."}, status=400)
    return JsonResponse(learning.answer(request, current, question, choice))


@login_required
def exam(request, slug):
    course = _course(slug)
    lessons = learning.lessons_with_state(request.user, course)
    if not learning.exam_open(lessons):
        messages.info(request, "Réussissez toutes les leçons pour ouvrir l'examen final.")
        return redirect(course.get_absolute_url())
    questions = list(course.questions.filter(lesson__isnull=True))
    attempt = gain = None
    if request.method == "POST":
        score, answers = quiz.grade(questions, request.POST)
        attempt = ExamAttempt.objects.create(
            user=request.user, course=course, score=score, total=len(questions), answers=answers
        )
        gain = learning.reward_exam(request.user, course, attempt)
        for question in questions:
            question.given = answers.get(str(question.pk))
    return render(request, "courses/exam.html", {
        "course": course, "questions": questions, "attempt": attempt, "gain": gain,
        "no_assistant": attempt is None, "pass": learning.PASS_LABELS,
    })
