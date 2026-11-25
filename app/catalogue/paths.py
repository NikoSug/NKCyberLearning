"""
Parcours de lecture : pour chaque thème (domaine), les guides en français rangés
par niveau (débutant → intermédiaire → avancé), dans un ordre de lecture logique.

Dans un niveau, on lit d'abord les documents courts (synthèses, « essentiels »)
puis les plus longs : chaque lecture prépare la suivante. Les parcours sont
calculés à partir du catalogue : un guide importé rejoint automatiquement le
parcours de son domaine, au bon niveau.

Les parcours sont des propositions : l'utilisateur choisit d'ajouter un parcours
(ou un seul niveau) à sa lecture.
"""
from dataclasses import dataclass, field

from .models import GuideCategory, Language, Level, _duration

LEVELS = [Level.BEGINNER, Level.INTERMEDIATE, Level.ADVANCED]


@dataclass
class Stage:
    """Un niveau d'un parcours."""
    level: str
    label: str
    guides: list = field(default_factory=list)

    @property
    def minutes(self):
        return sum(g.estimated_minutes or 0 for g in self.guides)

    @property
    def duration_display(self):
        return _duration(self.minutes)

    @property
    def read(self):
        return sum(1 for g in self.guides if g.finished_at)

    @property
    def done(self):
        return bool(self.guides) and self.read == len(self.guides)

    @property
    def to_add(self):
        return [g for g in self.guides if not g.in_list]


@dataclass
class Path:
    category: object
    stages: list

    @property
    def guides(self):
        return [g for stage in self.stages for g in stage.guides]

    @property
    def total(self):
        return len(self.guides)

    @property
    def read(self):
        return sum(stage.read for stage in self.stages)

    @property
    def minutes(self):
        return sum(stage.minutes for stage in self.stages)

    @property
    def duration_display(self):
        return _duration(self.minutes)

    @property
    def percent(self):
        return round(100 * self.read / self.total) if self.total else 0

    @property
    def started(self):
        return any(g.in_list for g in self.guides)

    @property
    def done(self):
        return bool(self.total) and self.read == self.total

    @property
    def next_guide(self):
        """Prochain guide à lire dans l'ordre du parcours."""
        return next((g for g in self.guides if not g.finished_at), None)

    @property
    def to_add(self):
        return [g for g in self.guides if not g.in_list]


def reading_order(guides):
    return sorted(
        guides,
        key=lambda g: (
            LEVELS.index(g.level) if g.level in LEVELS else len(LEVELS),
            g.estimated_minutes or 0,
            g.title.lower(),
        ),
    )


def build(guides, categories=None, level=None):
    """
    Parcours à partir d'une liste de guides déjà annotés (`finished_at`, `in_list`).
    `level` : ne garder qu'un niveau ; les thèmes sans guide sont omis.
    """
    categories = list(categories if categories is not None else GuideCategory.objects.all())
    by_category = {}
    for guide in guides:
        if guide.language == Language.FR and guide.category_id:
            by_category.setdefault(guide.category_id, []).append(guide)
    paths = []
    for category in categories:
        ordered = reading_order(by_category.get(category.pk, []))
        stages = [Stage(value, label, [g for g in ordered if g.level == value])
                  for value, label in Level.choices if level in (None, "", value)]
        stages = [stage for stage in stages if stage.guides]
        if stages:
            paths.append(Path(category, stages))
    return paths


def in_progress(paths, limit=3):
    """Parcours commencés et non terminés, les plus avancés d'abord."""
    current = [p for p in paths if p.started and not p.done]
    return sorted(current, key=lambda p: -p.percent)[:limit]
