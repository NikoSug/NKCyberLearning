"""
Progression dans un cours :

  - la leçon 1 est ouverte ; la suivante s'ouvre quand le quiz de la précédente est réussi
    (LESSON_PASS : 3 bonnes réponses sur 4) ;
  - quiz de leçon : correction immédiate après chaque réponse. La première réponse donnée à
    une question est gardée en session : on ne peut pas la changer après avoir vu la correction ;
  - l'examen final s'ouvre quand toutes les leçons sont réussies (EXAM_PASS : 70 %).

Barème : leçon réussie 15 XP (+5 sans faute), une seule fois par leçon ; examen réussi 40 XP
(+10 sans faute) et 10 émeraudes, une seule fois par cours.
"""
from django.utils import timezone

from progression import emeralds
from progression.models import EmeraldEvent, XPEvent
from progression.xp import Award, grant

from .models import EXAM_PASS, LESSON_PASS, Course, ExamAttempt, LessonProgress

DONE, OPEN, LOCKED = "done", "open", "locked"
LESSON_XP, LESSON_PERFECT = 15, 5
EXAM_XP, EXAM_PERFECT, EXAM_EMERALDS = 40, 10, 10


def published():
    return Course.objects.filter(status=Course.Status.PUBLISHED)


def lessons_with_state(user, course):
    """Leçons du cours avec leur état pour l'utilisateur (faite, ouverte, verrouillée) et son
    meilleur score."""
    lessons = list(course.lessons.all())
    progress = {
        p.lesson_id: p for p in LessonProgress.objects.filter(user=user, lesson__course=course)
    }
    previous_done = True
    for lesson in lessons:
        item = progress.get(lesson.pk)
        lesson.best = item.best_score if item else None
        lesson.state = DONE if item and item.passed_at else OPEN if previous_done else LOCKED
        previous_done = lesson.state == DONE
    return lessons


def course_progress(user, courses):
    """{cours: (leçons réussies, leçons)} pour une liste de cours."""
    passed = {}
    for course_id in LessonProgress.objects.filter(
        user=user, lesson__course__in=courses, passed_at__isnull=False
    ).values_list("lesson__course", flat=True):
        passed[course_id] = passed.get(course_id, 0) + 1
    return passed


def exam_open(lessons):
    return bool(lessons) and all(lesson.state == DONE for lesson in lessons)


def best_exam(user, course):
    attempts = list(ExamAttempt.objects.filter(user=user, course=course))
    return max(attempts, key=lambda a: a.score / a.total if a.total else 0, default=None)


# --- Quiz d'une leçon -------------------------------------------------------------------------
def _session_key(lesson):
    return f"cours:lecon:{lesson.pk}"


def start_quiz(request, lesson):
    request.session[_session_key(lesson)] = {}


def answer(request, lesson, question, choice):
    """Enregistre la première réponse à une question et renvoie la correction."""
    answers = request.session.get(_session_key(lesson), {})
    key = str(question.pk)
    if key not in answers:
        answers[key] = choice
        request.session[_session_key(lesson)] = answers
    given = answers[key]
    return {"correct": given == question.answer, "given": given, "answer": question.answer,
            "explanation": question.explanation}


def finish_quiz(request, lesson, questions, posted=None):
    """
    Corrige le quiz (réponses gardées en session, ou envoyées d'un coup sans JavaScript) ;
    enregistre la progression et les récompenses. Renvoie (score, total, réussi, gain, réponses).
    """
    answers = dict(request.session.pop(_session_key(lesson), {}))
    for question in questions:
        raw = (posted or {}).get(f"q{question.pk}")
        if str(question.pk) not in answers and raw is not None and str(raw).isdigit():
            answers[str(question.pk)] = int(raw)
    score = sum(answers.get(str(q.pk)) == q.answer for q in questions)
    total = len(questions)
    passed = bool(total) and score / total >= LESSON_PASS
    progress, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
    progress.attempts += 1
    progress.best_score = max(progress.best_score, round(100 * score / total) if total else 0)
    first_success = passed and progress.passed_at is None
    if first_success:
        progress.passed_at = timezone.now()
    progress.save()
    gain = None
    if passed:
        awards = [
            Award(
                f"lecon:{lesson.pk}",
                LESSON_XP,
                XPEvent.Kind.LESSON,
                f"Leçon réussie : {lesson.title}",
            )
        ]
        if score == total:
            awards.append(Award(f"lecon:{lesson.pk}:parfait", LESSON_PERFECT, XPEvent.Kind.LESSON,
                                f"Leçon sans faute : {lesson.title}"))
        gain = grant(request.user, awards)
    return score, total, passed, gain, answers


# --- Examen final -------------------------------------------------------------------------------
def reward_exam(user, course, attempt):
    if not attempt.passed:
        return None
    awards = [
        Award(f"examen:{course.pk}", EXAM_XP, XPEvent.Kind.TEST, f"Cours terminé : {course.title}")
    ]
    if attempt.score == attempt.total:
        awards.append(Award(f"examen:{course.pk}:parfait", EXAM_PERFECT, XPEvent.Kind.TEST,
                            f"Examen sans faute : {course.title}"))
    gain = grant(user, awards)
    gain.emeralds += emeralds.credit(
        user,
        f"cours:{course.pk}",
        EXAM_EMERALDS,
        EmeraldEvent.Kind.COMPLETION,
        f"Cours terminé : {course.title}",
    )
    return gain


PASS_LABELS = {"lesson": round(LESSON_PASS * 100), "exam": round(EXAM_PASS * 100)}
