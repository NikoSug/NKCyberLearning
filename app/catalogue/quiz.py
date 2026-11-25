"""
Tests de connaissances sur les guides et les référentiels ANSSI (`guide` : l'un ou l'autre).

Règles d'accès :
  - le test d'un guide n'est accessible qu'une fois le guide lu ;
  - la série 1 s'ouvre dès la lecture, puis une seule nouvelle série par jour :
    la série N+1 s'ouvre le lendemain du jour où la série N a été faite pour la
    première fois (un jour manqué ne fait rien perdre, mais on ne peut pas avancer) ;
  - une série déjà faite peut être refaite à volonté ; le meilleur score est retenu.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, IntegerField, Max, Min, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import QuizAttempt, QuizQuestion, ReadingListItem, subject_filter

DONE, AVAILABLE, LOCKED = "done", "available", "locked"


@dataclass
class Series:
    number: int
    state: str
    best: int | None = None      # meilleur score en %
    unlock_on: object = None     # date d'ouverture quand elle est connue

    @property
    def is_open(self):
        return self.state in (DONE, AVAILABLE)


def series_numbers(guide):
    return list(
        QuizQuestion.objects.filter(**subject_filter(guide))
        .values_list("series", flat=True)
        .distinct()
        .order_by("series")
    )


def annotate_series_count(queryset):
    """Ajoute `quiz_series` (nombre de séries de questions) à une liste de guides."""
    counts = (QuizQuestion.objects.filter(guide=OuterRef("pk")).order_by().values("guide")
              .annotate(n=Count("series", distinct=True)).values("n"))
    return queryset.annotate(
        quiz_series=Coalesce(Subquery(counts, output_field=IntegerField()), Value(0))
    )


def series_counts(guide_ids):
    """Nombre de séries de questions par guide, pour des guides déjà chargés."""
    return dict(QuizQuestion.objects.filter(guide_id__in=guide_ids).order_by().values("guide_id")
                .annotate(n=Count("series", distinct=True)).values_list("guide_id", "n"))


def is_read(user, guide):
    return ReadingListItem.objects.filter(
        user=user, **subject_filter(guide), finished_at__isnull=False
    ).exists()


def series_status(user, guide, numbers=None, read=None):
    """État de chaque série du guide pour l'utilisateur."""
    numbers = series_numbers(guide) if numbers is None else numbers
    read = is_read(user, guide) if read is None else read
    today = timezone.localdate()
    attempts = {row["series"]: row for row in
                QuizAttempt.objects.filter(user=user, **subject_filter(guide)).values("series")
                .annotate(first=Min("created_at"), best_score=Max("score"), total=Max("total"))}

    result, previous_first_day = [], None
    for index, number in enumerate(numbers):
        done = attempts.get(number)
        if done:
            best = round(100 * done["best_score"] / done["total"]) if done["total"] else 0
            result.append(Series(number, DONE, best=best))
            previous_first_day = timezone.localdate(done["first"])
            continue
        if not read:
            result.append(Series(number, LOCKED))
        elif index == 0 or (previous_first_day and previous_first_day < today):
            result.append(Series(number, AVAILABLE))
        elif previous_first_day:  # série précédente faite aujourd'hui : ouverture demain
            result.append(Series(number, LOCKED, unlock_on=previous_first_day + timedelta(days=1)))
        else:
            result.append(Series(number, LOCKED))
        previous_first_day = None
    return result


def can_take(user, guide, number):
    return any(s.number == number and s.is_open for s in series_status(user, guide))


def grade(questions, submitted):
    """Corrige une série : renvoie (score, réponses données {id: indice ou None})."""
    answers, score = {}, 0
    for question in questions:
        raw = submitted.get(f"q{question.pk}")
        choice = int(raw) if raw is not None and raw.isdigit() else None
        if choice is not None and not 0 <= choice < len(question.choices):
            choice = None
        answers[str(question.pk)] = choice
        score += choice == question.answer
    return score, answers
