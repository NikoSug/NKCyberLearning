"""
Barème d'XP des activités du catalogue (voir progression/xp.py pour les niveaux et la série).

  Lecture terminée          10 XP + 1 XP par tranche de 3 min de lecture estimée (60 XP max)
  Série de test             10 XP la première fois que la série est faite
                            +2 XP par question réussie pour la première fois
                            +5 XP la première fois que la série est faite sans faute
  Révision du jour          5 XP par jour où elle est faite
                            +2 XP par question réussie (une fois par question et par jour)
                            +5 XP si elle est faite sans faute (une fois par jour)

Refaire une série rapporte donc uniquement les questions enfin réussies : on
progresse en corrigeant ses erreurs, pas en répétant ce qu'on sait déjà.

Émeraudes (en plus de celles liées à l'XP, voir progression/emeralds.py) :
série sans faute (première fois), révision sans faute (une fois par jour),
test d'un guide terminé (toutes ses séries faites).
"""
from progression import emeralds
from progression import xp as progression
from progression.models import EmeraldEvent, XPEvent
from progression.xp import Award, grant

from .models import (
    DailyReviewAttempt,
    Guide,
    QuizAttempt,
    QuizQuestion,
    ReadingListItem,
    subject_filter,
)

READING_BASE, READING_MINUTES_PER_XP, READING_MAX = 10, 3, 60
SERIES_FIRST, QUESTION_XP, PERFECT_BONUS = 10, 2, 5
REVIEW_DAY = 5

Kind = XPEvent.Kind


def reading_xp(document):
    return min(
        READING_MAX, READING_BASE + (document.estimated_minutes or 0) // READING_MINUTES_PER_XP
    )


def reward_reading(user, document, when=None):
    kind = "guide" if isinstance(document, Guide) else "referentiel"
    return grant(user, [Award(f"lecture:{kind}:{document.pk}", reading_xp(document), Kind.READING,
                              f"Lecture terminée : {document.title}")], when=when)


def _correct(questions, answers):
    return [q for q in questions if answers.get(str(q.pk)) == q.answer]


def reward_series(user, guide, number, questions, answers, when=None):
    """Série d'un test de guide ou de référentiel (`guide` : l'un ou l'autre)."""
    # Référentiel : références « ref3 » pour ne pas se confondre avec celles du guide n° 3
    key, title, by = (
        guide.pk if isinstance(guide, Guide) else f"ref{guide.pk}",
        str(guide),
        subject_filter(guide),
    )
    correct = _correct(questions, answers)
    awards = [Award(f"serie:{key}:{number}", SERIES_FIRST, Kind.TEST, f"Série {number} : {title}")]
    awards += [
        Award(
            f"question:{q.pk}", QUESTION_XP, Kind.TEST, f"Bonne réponse — série {number} : {title}"
        )
        for q in correct
    ]
    perfect = bool(questions) and len(correct) == len(questions)
    if perfect:
        awards.append(Award(f"serie:{key}:{number}:parfait", PERFECT_BONUS, Kind.TEST,
                            f"Sans faute — série {number} : {title}"))
    gain = grant(user, awards, when=when)
    if perfect:
        gain.emeralds += emeralds.credit(
            user,
            f"parfait:serie:{key}:{number}",
            emeralds.SERIES_PERFECT,
            EmeraldEvent.Kind.PERFECT,
            f"Sans faute — série {number} : {title}",
            when,
        )
    series_count = QuizQuestion.objects.filter(**by).values("series").distinct().count()
    done = QuizAttempt.objects.filter(user=user, **by).values("series").distinct().count()
    if series_count and done >= series_count:
        gain.emeralds += emeralds.credit(
            user,
            f"guide:{key}",
            emeralds.GUIDE_COMPLETED,
            EmeraldEvent.Kind.COMPLETION,
            f"Test terminé : {title}",
            when,
        )
    return gain


def reward_review(user, review, questions, answers, when=None):
    # Seconde révision : références distinctes (« 2026-10-12-2 ») pour être récompensée elle aussi.
    day = review.date.isoformat() + (f"-{review.number}" if review.number > 1 else "")
    correct = _correct(questions, answers)
    awards = [
        Award(f"revision:{day}", REVIEW_DAY, Kind.REVIEW, f"Révision du {review.date:%d/%m/%Y}")
    ]
    awards += [
        Award(
            f"revision:{day}:{q.pk}",
            QUESTION_XP,
            Kind.REVIEW,
            f"Bonne réponse — révision du {review.date:%d/%m/%Y}",
        )
        for q in correct
    ]
    perfect = bool(questions) and len(correct) == len(questions)
    if perfect:
        awards.append(Award(f"revision:{day}:parfait", PERFECT_BONUS, Kind.REVIEW,
                            f"Sans faute — révision du {review.date:%d/%m/%Y}"))
    gain = grant(user, awards, when=when)
    if perfect:
        gain.emeralds += emeralds.credit(
            user,
            f"parfait:revision:{day}",
            emeralds.REVIEW_PERFECT,
            EmeraldEvent.Kind.PERFECT,
            f"Révision du {review.date:%d/%m/%Y} sans faute",
            when,
        )
    return gain


def backfill(user):
    """Attribue les XP des activités passées (à leur date d'origine). Sans effet si déjà fait."""
    gained = 0
    for item in ReadingListItem.objects.filter(user=user, finished_at__isnull=False).select_related(
        "guide", "referential"
    ):
        gained += reward_reading(user, item.document, when=item.finished_at).xp
    questions = {}
    for attempt in (
        QuizAttempt.objects.filter(user=user)
        .select_related("guide", "referential")
        .order_by("created_at")
    ):
        key = (attempt.guide_id, attempt.referential_id, attempt.series)
        if key not in questions:
            questions[key] = list(
                QuizQuestion.objects.filter(
                    **subject_filter(attempt.subject), series=attempt.series
                )
            )
        gained += reward_series(
            user,
            attempt.subject,
            attempt.series,
            questions[key],
            attempt.answers,
            when=attempt.created_at,
        ).xp
    for attempt in (
        DailyReviewAttempt.objects.filter(review__user=user)
        .select_related("review")
        .order_by("created_at")
    ):
        review_questions = list(QuizQuestion.objects.filter(pk__in=attempt.review.question_ids))
        gained += reward_review(
            user, attempt.review, review_questions, attempt.answers, when=attempt.created_at
        ).xp
    # Émeraudes des niveaux déjà atteints (si l'XP avait été gagné avant leur création).
    emeralds.after_xp(user, 1, progression.level_for(progression.total_xp(user)))
    return gained
