"""
Révision du jour : un questionnaire quotidien qui mélange les questions de tous les
guides lus par l'utilisateur.

Questions éligibles : celles des séries que l'utilisateur a déjà faites au moins une
fois. On ne pioche pas dans les séries encore fermées, pour ne pas dévoiler à l'avance
les questions du test progressif de chaque guide.

Choix des questions (répétition espacée, inspirée de la méthode Leitner) :
  - chaque question a un historique de réponses (séries et révisions confondues) ;
  - après une bonne réponse, la question revient d'autant plus tard que la série de
    bonnes réponses consécutives est longue : 2, 4, 8, 16 puis 30 jours ;
  - une question ratée la dernière fois revient en priorité ;
  - viennent ensuite les questions arrivées à échéance (les plus en retard d'abord),
    puis, pour compléter, celles dont l'échéance est la plus proche ;
  - les questions déjà vues aujourd'hui passent en dernier recours.

Les guides sont mélangés : chaque guide est plafonné lors d'un premier passage, puis
l'ordre final est tiré au hasard. Le tirage dépend de l'utilisateur et du jour, et la
sélection est enregistrée : elle ne change pas si l'on recharge la page.
"""
import math
import random
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import DailyReview, DailyReviewAttempt, QuizAttempt, QuizQuestion, ReadingListItem

REVIEW_SIZE = 10
MAX_INTERVAL = 30  # jours
WRONG, DUE, LATER, SEEN_TODAY = range(4)  # ordre de priorité


@dataclass
class History:
    streak: int = 0          # bonnes réponses consécutives les plus récentes
    last_seen: object = None
    last_correct: bool = True

    def interval(self):
        return min(2 ** self.streak, MAX_INTERVAL)


def eligible_questions(user):
    """Questions des séries déjà faites, sur les guides lus."""
    read = ReadingListItem.objects.filter(user=user, guide__isnull=False, finished_at__isnull=False)
    done = set(QuizAttempt.objects.filter(user=user, guide__in=read.values("guide"))
               .values_list("guide_id", "series").distinct())
    if not done:
        return []
    questions = QuizQuestion.objects.filter(guide__in={g for g, _ in done}).select_related("guide")
    return [q for q in questions if (q.guide_id, q.series) in done]


def answer_history(user, questions):
    """Historique des réponses de l'utilisateur pour chaque question, du plus ancien au plus
    récent."""
    by_id = {str(q.pk): q for q in questions}
    events = []
    for attempt in QuizAttempt.objects.filter(user=user).only("answers", "created_at"):
        events += [(attempt.created_at, qid, given) for qid, given in attempt.answers.items()]
    for attempt in DailyReviewAttempt.objects.filter(review__user=user).only(
        "answers", "created_at"
    ):
        events += [(attempt.created_at, qid, given) for qid, given in attempt.answers.items()]

    history = {q.pk: History() for q in questions}
    for when, qid, given in sorted(events, key=lambda e: e[0]):
        question = by_id.get(qid)
        if question is None:
            continue
        entry = history[question.pk]
        correct = given == question.answer
        entry.streak = entry.streak + 1 if correct else 0
        entry.last_correct = correct
        entry.last_seen = timezone.localdate(when)
    return history


def priority(entry, today):
    """Clé de tri : (rang, urgence). Plus petit = plus prioritaire."""
    if entry.last_seen is None:
        return DUE, 0  # jamais répondue (cas rare : série faite avec des questions ajoutées depuis)
    if entry.last_seen == today:
        return SEEN_TODAY, 0
    if not entry.last_correct:
        return WRONG, -(today - entry.last_seen).days
    due_on = entry.last_seen + timedelta(days=entry.interval())
    if due_on <= today:
        return DUE, -(today - due_on).days  # les plus en retard d'abord
    return LATER, (due_on - today).days  # échéance la plus proche d'abord


def select_questions(user, today, size=REVIEW_SIZE, exclude=(), number=1):
    """`exclude` : questions déjà posées aujourd'hui (seconde révision) ; `number` varie le
    tirage."""
    questions = [q for q in eligible_questions(user) if q.pk not in exclude]
    if not questions:
        return []
    history = answer_history(user, questions)
    seed = f"revision-{user.pk}-{today.isoformat()}" + (f"-{number}" if number > 1 else "")
    rng = random.Random(seed)
    rng.shuffle(questions)  # départage aléatoire (mais reproductible) des égalités
    questions.sort(key=lambda q: priority(history[q.pk], today))

    guides = {q.guide_id for q in questions}
    cap = max(2, math.ceil(size / len(guides)))
    chosen, per_guide = [], {}
    for question in questions:  # premier passage : chaque guide est plafonné
        if len(chosen) == size:
            break
        if per_guide.get(question.guide_id, 0) < cap:
            chosen.append(question)
            per_guide[question.guide_id] = per_guide.get(question.guide_id, 0) + 1
    for question in questions:  # second passage : on complète si besoin
        if len(chosen) == size:
            break
        if question not in chosen:
            chosen.append(question)
    rng.shuffle(chosen)
    return chosen


MIN_SECOND_REVIEW = 3  # questions nouvelles nécessaires pour proposer une seconde révision


def todays_review(user, create=True, number=1):
    """
    Révision du jour de l'utilisateur (number=2 : seconde révision achetée en boutique,
    avec des questions différentes de la première), créée au premier accès.
    """
    today = timezone.localdate()
    review = DailyReview.objects.filter(user=user, date=today, number=number).first()
    if review or not create:
        return review
    exclude = set()
    for earlier in DailyReview.objects.filter(user=user, date=today, number__lt=number):
        exclude.update(earlier.question_ids)
    chosen = select_questions(user, today, exclude=exclude, number=number)
    if not chosen:
        return None
    try:
        with transaction.atomic():
            return DailyReview.objects.create(user=user, date=today, number=number,
                                              question_ids=[q.pk for q in chosen])
    except IntegrityError:  # deux requêtes simultanées : on garde la première
        return DailyReview.objects.get(user=user, date=today, number=number)


def second_review_possible(user):
    """La révision du jour est faite et il reste assez de questions nouvelles pour une seconde."""
    first = todays_review(user, create=False)
    if first is None or not first.attempts.exists():
        return False
    remaining = [q for q in eligible_questions(user) if q.pk not in set(first.question_ids)]
    return len(remaining) >= MIN_SECOND_REVIEW


def review_questions(review):
    """Questions de la révision, dans l'ordre enregistré (celles supprimées depuis sont
    ignorées)."""
    found = (
        QuizQuestion.objects.filter(pk__in=review.question_ids).select_related("guide").in_bulk()
    )
    return [found[pk] for pk in review.question_ids if pk in found]


def streak_days(user):
    """Nombre de jours consécutifs (jusqu'à aujourd'hui ou hier) avec une révision faite."""
    days = set(DailyReview.objects.filter(user=user, attempts__isnull=False)
               .values_list("date", flat=True))
    day = timezone.localdate()
    if day not in days:
        day -= timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count
