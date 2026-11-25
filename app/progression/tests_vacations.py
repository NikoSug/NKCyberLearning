"""Mode vacances : série protégée, aucune lecture planifiée, réglage depuis Sécurité et données."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue import reading
from catalogue.models import Guide, StudyPlan
from catalogue.planning import build_schedule

from . import vacations, xp
from .models import LearnerProfile, StreakFreezeUse, Vacation

MONDAY = date(2026, 10, 19)


class VacationStreakTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("lea")

    def test_vacation_days_keep_the_streak_without_using_freezes(self):
        profile = LearnerProfile.objects.get_or_create(user=self.user)[0]
        profile.streak_freezes = 2
        profile.save()
        Vacation.objects.create(
            user=self.user, start=MONDAY - timedelta(days=5), end=MONDAY - timedelta(days=1)
        )
        totals = {
            MONDAY - timedelta(days=6): 20,
            MONDAY - timedelta(days=7): 20,
        }  # deux jours avant les vacances
        self.assertEqual(xp.streak_for(self.user, totals, MONDAY), (2, False))
        self.assertFalse(StreakFreezeUse.objects.exists())
        self.assertEqual(LearnerProfile.objects.get(user=self.user).streak_freezes, 2)

    def test_learning_during_vacation_extends_the_streak(self):
        Vacation.objects.create(user=self.user, start=MONDAY - timedelta(days=3), end=MONDAY)
        totals = {MONDAY - timedelta(days=4): 20, MONDAY - timedelta(days=2): 15}
        self.assertEqual(xp.streak_for(self.user, totals, MONDAY), (2, False))

    def test_start_and_stop(self):
        today = timezone.localdate()
        vacations.start(self.user, today + timedelta(days=10))
        self.assertEqual(vacations.current(self.user).end, today + timedelta(days=10))
        vacations.start(
            self.user, today + timedelta(days=3)
        )  # nouvelle date de retour : même période
        self.assertEqual(Vacation.objects.count(), 1)
        vacations.stop(self.user)  # retour le jour même du départ : la période disparaît
        self.assertFalse(Vacation.objects.exists())
        Vacation.objects.create(
            user=self.user, start=today - timedelta(days=4), end=today + timedelta(days=4)
        )
        vacations.stop(self.user)  # retour anticipé : les jours passés restent protégés
        self.assertEqual(
            list(Vacation.objects.values_list("start", "end")),
            [(today - timedelta(days=4), today - timedelta(days=1))],
        )


class VacationPlanningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tom")
        self.client.force_login(self.user)

    def test_no_reading_planned_during_vacation(self):
        guides = [type("G", (), {"title": n, "estimated_minutes": 30})() for n in "abc"]
        off = {MONDAY, MONDAY + timedelta(days=1)}
        days = build_schedule(guides, 30, today=MONDAY, days_off=off)
        self.assertEqual([d.date for d in days], [MONDAY + timedelta(days=n) for n in (2, 3, 4)])

    def test_security_page_activates_and_ends_vacation(self):
        today = timezone.localdate()
        page = self.client.get(reverse("accounts:security"))
        self.assertContains(page, "Activer le mode vacances")
        refused = self.client.post(
            reverse("accounts:vacation"), {"end": today + timedelta(days=200)}, follow=True
        )
        self.assertContains(refused, "90 jours au plus")
        self.assertFalse(Vacation.objects.exists())
        self.client.post(reverse("accounts:vacation"), {"end": today + timedelta(days=7)})
        self.assertEqual(vacations.current(self.user).end, today + timedelta(days=7))
        self.assertContains(self.client.get(reverse("accounts:security")), "Je suis de retour")

        guide = Guide.objects.create(title="Pare-feu", slug="pare-feu", estimated_minutes=30)
        reading.add(self.user, guide)
        StudyPlan.objects.create(user=self.user, minutes_per_day=30)
        dashboard = self.client.get(reverse("catalogue:dashboard"))
        self.assertContains(dashboard, "Bonnes vacances !")
        self.assertEqual(dashboard.context["today_reading"], [])
        self.assertEqual(dashboard.context["next_reading_day"], today + timedelta(days=8))
        summary = xp.summary(self.user)
        self.assertEqual(summary.vacation_until, today + timedelta(days=7))
        self.assertFalse(summary.streak_at_risk)

        self.client.post(reverse("accounts:vacation"), {"action": "retour"})
        self.assertIsNone(vacations.current(self.user))
        self.assertEqual(
            self.client.get(reverse("catalogue:dashboard")).context["today_reading"][0].document,
            guide,
        )
