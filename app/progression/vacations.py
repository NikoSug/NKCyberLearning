"""
Mode vacances : périodes pendant lesquelles l'utilisateur ne lit pas. Aucune lecture n'y est
planifiée, la série
de jours ne peut pas s'y casser et aucun gel de série n'y est consommé ; apprendre quand même la
prolonge.
"""

from datetime import timedelta

from django.utils import timezone

from .models import Vacation

MAX_DAYS = 90


def current(user, today=None):
    """Période de vacances en cours (ou None)."""
    today = today or timezone.localdate()
    return (
        Vacation.objects.filter(user=user, start__lte=today, end__gte=today)
        .order_by("-end")
        .first()
    )


def upcoming_or_current(user, today=None):
    today = today or timezone.localdate()
    return Vacation.objects.filter(user=user, end__gte=today).order_by("start").first()


def days_off(user, since=None, until=None):
    """Toutes les dates couvertes par des vacances entre `since` et `until` (bornes incluses)."""
    today = timezone.localdate()
    since = since or today - timedelta(days=400)
    until = until or today + timedelta(days=400)
    dates = set()
    for start, end in Vacation.objects.filter(
        user=user, end__gte=since, start__lte=until
    ).values_list("start", "end"):
        day = max(start, since)
        while day <= min(end, until):
            dates.add(day)
            day += timedelta(days=1)
    return frozenset(dates)


def start(user, end, today=None):
    """Active le mode vacances d'aujourd'hui jusqu'à `end` (inclus) ; remplace une période en cours
    ou à venir."""
    today = today or timezone.localdate()
    Vacation.objects.filter(user=user, start__gte=today).delete()
    running = current(user, today)
    if running:
        running.end = end
        running.save(update_fields=["end"])
        return running
    return Vacation.objects.create(user=user, start=today, end=end)


def stop(user, today=None):
    """Retour de vacances : aujourd'hui redevient un jour normal (la période se termine hier)."""
    today = today or timezone.localdate()
    Vacation.objects.filter(user=user, start__gt=today).delete()
    for vacation in Vacation.objects.filter(user=user, start__lte=today, end__gte=today):
        if vacation.start == today:
            vacation.delete()
        else:
            vacation.end = today - timedelta(days=1)
            vacation.save(update_fields=["end"])
