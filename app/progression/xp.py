"""
Moteur de progression : XP, niveaux, objectif quotidien et série de jours.

Principes (inspirés de Duolingo) :
  - chaque action d'apprentissage rapporte des XP, une seule fois (référence unique) ;
  - les niveaux demandent de plus en plus d'XP : passer du niveau N au niveau N+1
    demande 100 × N XP (100, 200, 300…) ;
  - l'objectif quotidien (10 à 100 XP) se règle par l'utilisateur ;
  - la série (« streak ») compte les jours consécutifs avec au moins STREAK_MIN XP.
    Elle reste affichée tant que la journée d'hier est validée : il suffit
    d'apprendre aujourd'hui pour la prolonger. Un gel de série (boutique) couvre
    un jour manqué : la série continue, sans augmenter ce jour-là. Les jours absents
    du planning de lecture sont des jours de repos : ils ne la cassent pas (et y
    apprendre quand même la fait augmenter) ;
  - une potion double XP (boutique) double les gains pendant sa durée.

Les barèmes propres à chaque activité (lecture, tests…) sont définis par les
modules qui appellent `grant` (voir catalogue/rewards.py).
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from . import avatars
from . import items
from . import vacations
from .models import EmeraldEvent, LearnerProfile, StreakFreezeUse, XPEvent

STREAK_MIN = 10  # XP minimum dans la journée pour qu'elle compte dans la série
LEVEL_TITLES = [
    "Recrue", "Stagiaire SSI", "Analyste junior", "Analyste SOC", "Analyste senior", "Pentester",
    "Threat hunter", "Architecte sécurité", "Expert CERT", "RSSI", "Référence cyber",
]


def level_start(level):
    """XP total nécessaire pour atteindre un niveau (niveau 1 : 0 XP)."""
    return 50 * level * (level - 1)


def level_for(total):
    level = 1
    while total >= level_start(level + 1):
        level += 1
    return level


def level_title(level):
    return LEVEL_TITLES[min(level, len(LEVEL_TITLES)) - 1]


@dataclass
class Award:
    ref: str
    amount: int
    kind: str
    label: str


@dataclass
class Gain:
    """Résultat d'un ensemble de gains : XP obtenus et éventuel changement de niveau."""
    xp: int = 0
    level_before: int = 1
    level_after: int = 1
    emeralds: int = 0
    doubled: bool = False

    @property
    def level_up(self):
        return self.level_after > self.level_before

    @property
    def title(self):
        return level_title(self.level_after)


def total_xp(user):
    return XPEvent.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0


def double_xp_active(user, now=None):
    until = (
        LearnerProfile.objects.filter(user=user).values_list("double_xp_until", flat=True).first()
    )
    return bool(until and until > (now or timezone.now()))


def grant(user, awards, when=None):
    """
    Enregistre des gains d'XP ; ceux déjà obtenus (même référence) sont ignorés.
    Les gains en direct (sans `when`) sont doublés pendant une potion double XP.
    """
    before = total_xp(user)
    gained = 0
    factor = 2 if when is None and double_xp_active(user) else 1
    for award in awards:
        if award.amount <= 0:
            continue
        amount = award.amount * factor
        label = f"{award.label} (double XP)" if factor > 1 else award.label
        try:
            with transaction.atomic():
                XPEvent.objects.create(
                    user=user,
                    ref=award.ref[:120],
                    amount=amount,
                    kind=award.kind,
                    label=label[:200],
                    created_at=when or timezone.now(),
                )
            gained += amount
        except IntegrityError:  # déjà obtenu
            pass
    gain = Gain(
        xp=gained,
        level_before=level_for(before),
        level_after=level_for(before + gained),
        doubled=factor > 1,
    )
    if gained:
        from . import emeralds  # import local : emeralds s'appuie sur ce module
        gain.emeralds = emeralds.after_xp(user, gain.level_before, gain.level_after, when=when)
    return gain


def profile(user):
    return LearnerProfile.objects.get_or_create(user=user)[0]


def daily_totals(user, days=None):
    """XP par jour {date: XP}, éventuellement limités aux `days` derniers jours."""
    events = XPEvent.objects.filter(user=user)
    if days:
        start = timezone.localdate() - timedelta(days=days - 1)
        events = events.filter(created_at__date__gte=start)
    return dict(events.annotate(day=TruncDate("created_at")).values("day")
                .annotate(xp=Sum("amount")).values_list("day", "xp"))


def streak(totals, today=None, frozen=(), rest=(), off=()):
    """
    (nombre de jours, journée d'aujourd'hui déjà validée ?). Les jours `frozen`
    (gels de série) et les jours de repos (`rest` : numéros de jour de la semaine,
    0 = lundi, hors planning de lecture) et les jours de vacances (`off` : dates) prolongent la
    série sans la
    faire augmenter ;
    apprendre un jour de repos la fait augmenter quand même (bonus).
    """
    today = today or timezone.localdate()
    rest = _usable(rest)
    done_today = totals.get(today, 0) >= STREAK_MIN
    day = today if done_today else today - timedelta(days=1)
    count = 0
    while totals.get(day, 0) >= STREAK_MIN or day in frozen or day.weekday() in rest or day in off:
        count += totals.get(day, 0) >= STREAK_MIN
        day -= timedelta(days=1)
    return count, done_today


def _usable(rest):
    """Jours de repos utilisables : jamais toute la semaine (la série ne voudrait plus rien
    dire)."""
    rest = frozenset(rest)
    return rest if len(rest) < 7 else frozenset()


def frozen_days(user):
    return set(StreakFreezeUse.objects.filter(user=user).values_list("date", flat=True))


def rest_weekdays(user):
    """
    Jours de repos de l'utilisateur : les jours de la semaine absents de son planning de lecture.
    Ils ne cassent
    pas la série et ne consomment pas de gel (« s'adapter au rythme de chacun », pas le pénaliser).
    """
    from catalogue.models import StudyPlan  # import local : catalogue dépend de progression

    plan = StudyPlan.objects.filter(user=user).only("weekdays").first()
    reading = set(plan.weekdays or ()) if plan else set()
    return frozenset(set(range(7)) - reading) if reading else frozenset()


def apply_freezes(user, totals, today=None, rest=(), off=()):
    """
    Utilise automatiquement les gels de série en réserve pour couvrir les jours
    manqués depuis la dernière journée validée (les jours de repos et de vacances ne
    comptent pas comme manqués). Un gel n'est consommé que s'il y a une série à sauver et assez
    de gels pour couvrir tous les jours manqués. Renvoie l'ensemble des jours gelés.
    """
    today = today or timezone.localdate()
    rest = _usable(rest)
    frozen = frozen_days(user)

    def kept(day):
        return totals.get(day, 0) >= STREAK_MIN or day in frozen

    stock = items.get(items.STREAK_FREEZE).stock_max
    missed, day = [], today - timedelta(days=1)
    while not kept(day) and len(missed) <= stock:
        if day.weekday() not in rest and day not in off:
            missed.append(day)
        day -= timedelta(days=1)
    if not missed or len(missed) > stock or not kept(day):
        return frozen
    with transaction.atomic():
        learner = LearnerProfile.objects.select_for_update().filter(user=user).first()
        if learner is None or learner.streak_freezes < len(missed):
            return frozen
        for missed_day in missed:
            StreakFreezeUse.objects.get_or_create(user=user, date=missed_day)
        learner.streak_freezes -= len(missed)
        learner.save(update_fields=["streak_freezes"])
    return frozen | set(missed)


def streak_for(user, totals=None, today=None):
    """Série d'un utilisateur, jours de repos et gels de série compris (gels consommés si
    nécessaire)."""
    totals = daily_totals(user, days=400) if totals is None else totals
    rest, off = rest_weekdays(user), vacations.days_off(user)
    return streak(totals, today, apply_freezes(user, totals, today, rest, off), rest, off)


@dataclass
class Summary:
    total: int
    level: int
    title: str
    next_title: str
    level_xp: int        # XP gagnés depuis le début du niveau
    level_size: int      # XP nécessaires pour passer au niveau suivant
    today: int
    goal: int
    streak: int
    streak_today: bool
    week: list           # [(date, XP)] des 7 derniers jours
    emeralds: int = 0    # solde d'émeraudes (monnaie de la boutique)
    avatar: object = None
    frame: object = None          # objets équipés (progression/items.py)
    theme: object = None
    profile_title: object = None
    freezes: int = 0              # gels de série en réserve
    frozen_yesterday: bool = False
    rest_today: bool = False      # jour hors planning de lecture : la série ne risque rien
    vacation_until: object = None  # mode vacances en cours : jusqu'à cette date (incluse)
    double_xp_until: object = None

    @property
    def level_percent(self):
        return min(100, round(100 * self.level_xp / self.level_size)) if self.level_size else 100

    @property
    def to_next(self):
        return self.level_size - self.level_xp

    @property
    def goal_percent(self):
        return min(100, round(100 * self.today / self.goal)) if self.goal else 100

    @property
    def week_total(self):
        return sum(amount for _, amount in self.week)

    @property
    def goal_left(self):
        return max(0, self.goal - self.today)

    @property
    def goal_reached(self):
        return self.today >= self.goal

    @property
    def double_xp(self):
        return bool(self.double_xp_until and self.double_xp_until > timezone.now())

    @property
    def streak_at_risk(self):
        """Série en cours mais rien fait aujourd'hui (un jour de lecture) : elle s'arrêtera à
        minuit."""
        return (
            self.streak > 0
            and not self.streak_today
            and not self.rest_today
            and not self.vacation_until
        )


def summary(user):
    totals = daily_totals(user, days=400)
    total = total_xp(user)
    level = level_for(total)
    today = timezone.localdate()
    rest, off = rest_weekdays(user), vacations.days_off(user)
    frozen = apply_freezes(user, totals, today, rest, off)
    days, done_today = streak(totals, today, frozen, rest, off)
    holiday = vacations.current(user, today)
    learner = profile(user)
    emeralds = EmeraldEvent.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0
    return Summary(
        total=total,
        level=level,
        title=level_title(level),
        next_title=level_title(level + 1),
        level_xp=total - level_start(level),
        level_size=level_start(level + 1) - level_start(level),
        today=totals.get(today, 0),
        goal=learner.daily_goal,
        streak=days,
        streak_today=done_today,
        emeralds=emeralds,
        avatar=avatars.get(learner.avatar),
        frame=items.get(learner.frame, items.FRAME),
        theme=items.get(learner.theme, items.THEME),
        profile_title=items.get(learner.profile_title, items.TITLE),
        freezes=learner.streak_freezes,
        frozen_yesterday=today - timedelta(days=1) in frozen,
        double_xp_until=learner.double_xp_until,
        rest_today=today.weekday() in rest,
        vacation_until=holiday.end if holiday else None,
        week=[
            (today - timedelta(days=n), totals.get(today - timedelta(days=n), 0))
            for n in range(6, -1, -1)
        ],
    )
