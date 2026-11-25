"""Temps de lecture estimé d'un document, d'après son nombre de pages."""

MINUTES_PER_PAGE = 2.5  # lecture attentive d'un document technique
MIN_MINUTES = 10


def reading_minutes(pages):
    if not pages:
        return 30
    minutes = max(MIN_MINUTES, pages * MINUTES_PER_PAGE)
    return int(5 * round(minutes / 5))
