"""Jours de repos : les jours hors planning de lecture ne cassent pas la série et ne consomment pas
de gel."""

from datetime import date, datetime, time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalogue.models import StudyPlan

from . import xp
from .models import LearnerProfile, StreakFreezeUse, XPEvent

MONDAY = date(2026, 10, 19)
FRIDAY, SATURDAY, SUNDAY = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)
THURSDAY = date(2026, 10, 15)
WEEKEND = {5, 6}


class RestDayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("lea")
        StudyPlan.objects.create(user=self.user, minutes_per_day=30, weekdays=[0, 1, 2, 3, 4])

    def test_weekend_off_keeps_the_streak(self):
        totals = {THURSDAY: 20, FRIDAY: 20}
        self.assertEqual(xp.streak(totals, MONDAY), (0, False))  # sans jours de repos : cassée
        self.assertEqual(xp.streak(totals, MONDAY, rest=WEEKEND), (2, False))  # avec : intacte
        self.assertEqual(xp.rest_weekdays(self.user), frozenset(WEEKEND))
        self.assertEqual(xp.streak_for(self.user, totals, MONDAY), (2, False))

    def test_learning_on_a_rest_day_is_a_bonus(self):
        totals = {FRIDAY: 20, SATURDAY: 15}
        self.assertEqual(xp.streak(totals, MONDAY, rest=WEEKEND), (2, False))

    def test_a_missed_working_day_still_breaks_the_streak(self):
        totals = {THURSDAY: 20}  # vendredi manqué
        self.assertEqual(xp.streak(totals, MONDAY, rest=WEEKEND), (0, False))

    def test_freezes_cover_only_missed_working_days(self):
        profile = LearnerProfile.objects.get_or_create(user=self.user)[0]
        profile.streak_freezes = 1
        profile.save()
        totals = {THURSDAY: 20}  # vendredi manqué, puis week-end de repos
        self.assertEqual(xp.streak_for(self.user, totals, MONDAY), (1, False))
        self.assertEqual(list(StreakFreezeUse.objects.values_list("date", flat=True)), [FRIDAY])
        self.assertEqual(LearnerProfile.objects.get(user=self.user).streak_freezes, 0)

    def test_no_streak_warning_on_a_rest_day(self):
        friday_noon = timezone.make_aware(datetime.combine(FRIDAY, time(12)))
        XPEvent.objects.create(
            user=self.user,
            amount=20,
            kind="lecture",
            label="Lecture",
            ref="test",
            created_at=friday_noon,
        )
        with mock.patch.object(xp.timezone, "localdate", return_value=SATURDAY):
            summary = xp.summary(self.user)
        self.assertEqual((summary.streak, summary.rest_today), (1, True))
        self.assertFalse(summary.streak_at_risk)

    def test_without_plan_or_with_every_day_nothing_changes(self):
        other = get_user_model().objects.create_user("tom")
        self.assertEqual(xp.rest_weekdays(other), frozenset())
        StudyPlan.objects.create(user=other, minutes_per_day=30, weekdays=[0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(xp.rest_weekdays(other), frozenset())
        self.assertEqual(
            xp.streak({FRIDAY: 20}, MONDAY, rest=set(range(7))), (0, False)
        )  # jamais toute la semaine
