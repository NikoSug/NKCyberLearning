"""
Calcul du planning de lecture à partir du temps disponible chaque jour.

Le planning n'est pas stocké : il est recalculé à chaque affichage à partir
des documents encore non lus, en commençant aujourd'hui. Lire un document le
retire donc du planning, et un retard ne s'accumule jamais. Seuls les jours de la
semaine où l'utilisateur est disponible reçoivent des lectures.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from math import ceil

ALL_WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)   # lundi = 0 … dimanche = 6 (date.weekday())


def reading_dates(start, weekdays=ALL_WEEKDAYS, days_off=()):
    """
    Dates de lecture successives à partir de `start` (inclus), sur les seuls jours de la semaine
    choisis et hors
    vacances (`days_off`, dates).
    """
    weekdays = set(weekdays) or set(ALL_WEEKDAYS)
    current = start
    while True:
        if current.weekday() in weekdays and current not in days_off:
            yield current
        current += timedelta(days=1)


@dataclass
class Day:
    number: int
    date: date
    items: list = field(default_factory=list)
    minutes: int = 0
    continued: object = None  # document long dont la lecture se poursuit ce jour-là

    @property
    def week_start(self):
        return self.date - timedelta(days=self.date.weekday())


def build_schedule(items, minutes_per_day, today=None, weekdays=ALL_WEEKDAYS, days_off=()):
    """
    Répartit les documents (dans l'ordre fourni) en journées de `minutes_per_day`, sur les jours de
    la semaine
    `weekdays` seulement, hors vacances (à partir d'aujourd'hui, ou du prochain jour disponible).

    Un document plus long que le temps quotidien occupe à lui seul autant de
    jours que nécessaire : il apparaît le premier jour, les suivants indiquent
    sa poursuite (`continued`).
    """
    if minutes_per_day <= 0:
        raise ValueError("Le temps disponible doit être positif.")
    today = today or date.today()
    days, dates = [], reading_dates(today, weekdays, days_off)

    def new_day():
        day = Day(number=len(days) + 1, date=next(dates))
        days.append(day)
        return day

    current = None
    for item in items:
        duration = item.estimated_minutes
        if current is None or (current.items and current.minutes + duration > minutes_per_day):
            current = new_day()
        current.items.append(item)
        current.minutes += duration
        # Document trop long pour une journée : on réserve les jours suivants.
        extra_days = ceil(current.minutes / minutes_per_day) - 1
        for _ in range(extra_days):
            filler = new_day()
            filler.minutes = minutes_per_day
            filler.continued = item
        if extra_days:
            current = None
    return days
