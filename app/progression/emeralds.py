"""
Émeraudes : la monnaie de la boutique, distincte de l'XP.

L'XP mesure l'apprentissage (niveau) et ne se dépense jamais. Les émeraudes
récompensent la régularité et l'excellence, et servent à acheter des récompenses :

  Niveau atteint                10 × le nouveau niveau (niveau 2 : 20, niveau 5 : 50…)
  Objectif du jour atteint      5 par jour
  Paliers de série              3 j : 10 · 7 j : 25 · 14 j : 40 · 30 j : 100 · 60 j : 150 · 100 j :
  300 · 365 j : 1000
  Série de test sans faute      3 (la première fois)
  Révision du jour sans faute   2 (une fois par jour)
  Test d'un guide terminé       15 (les 6 séries faites)

Les récompenses liées à l'XP (niveau, objectif, série) sont versées automatiquement
à chaque gain d'XP (voir xp.grant) ; les autres par les modules concernés.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from . import vacations
from .models import EmeraldEvent

GOAL_REWARD = 5
STREAK_MILESTONES = {3: 10, 7: 25, 14: 40, 30: 100, 60: 150, 100: 300, 365: 1000}
SERIES_PERFECT = 3
REVIEW_PERFECT = 2
GUIDE_COMPLETED = 15

Kind = EmeraldEvent.Kind


def level_reward(level):
    return 10 * level


def balance(user):
    return EmeraldEvent.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0


def credit(user, ref, amount, kind, label, when=None):
    """Verse des émeraudes une seule fois par référence. Renvoie le montant versé (0 si déjà
    versé)."""
    try:
        with transaction.atomic():
            EmeraldEvent.objects.create(
                user=user,
                ref=ref[:120],
                amount=amount,
                kind=kind,
                label=label[:200],
                created_at=when or timezone.now(),
            )
    except IntegrityError:
        return 0
    return amount


def after_xp(user, level_before, level_after, when=None):
    """Récompenses déclenchées par un gain d'XP : niveaux franchis, objectif du jour, paliers de
    série."""
    from . import xp  # import local : xp appelle ce module

    gained = 0
    for level in range(level_before + 1, level_after + 1):
        gained += credit(
            user,
            f"niveau:{level}",
            level_reward(level),
            Kind.LEVEL,
            f"Niveau {level} atteint",
            when,
        )

    day = timezone.localdate(when) if when else timezone.localdate()
    totals = xp.daily_totals(user, days=400)
    if totals.get(day, 0) >= xp.profile(user).daily_goal:
        gained += credit(user, f"objectif:{day.isoformat()}", GOAL_REWARD, Kind.GOAL,
                         f"Objectif du {day:%d/%m/%Y} atteint", when)

    frozen, rest, off = xp.frozen_days(user), xp.rest_weekdays(user), vacations.days_off(user)
    count, _ = xp.streak(totals, day, frozen, rest, off)
    if count:
        # Premier jour de la série en cours : une même série ne verse chaque palier qu'une fois.
        start = day
        while True:
            before = start - timedelta(days=1)
            if not (
                totals.get(before, 0) >= xp.STREAK_MIN
                or before in frozen
                or before.weekday() in rest
                or before in off
            ):
                break
            start = before
        for days, reward in STREAK_MILESTONES.items():
            if count >= days:
                gained += credit(user, f"serie:{days}:{start.isoformat()}", reward, Kind.STREAK,
                                 f"Série de {days} jours", when)
    return gained


def history(user, limit=20):
    return list(EmeraldEvent.objects.filter(user=user)[:limit])
