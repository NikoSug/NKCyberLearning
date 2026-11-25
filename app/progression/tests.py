"""Progression : niveaux, gains uniques, objectif quotidien, série de jours, pages."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import xp
from .models import LearnerProfile, XPEvent

User = get_user_model()
Kind = XPEvent.Kind


class LevelTests(TestCase):
    def test_each_level_needs_more_xp(self):
        self.assertEqual([xp.level_start(n) for n in range(1, 6)], [0, 100, 300, 600, 1000])
        self.assertEqual(
            [xp.level_for(t) for t in (0, 99, 100, 299, 300, 5000)], [1, 1, 2, 2, 3, 10]
        )

    def test_titles_stop_at_the_last_one(self):
        self.assertEqual(xp.level_title(1), "Recrue")
        self.assertEqual(xp.level_title(50), xp.LEVEL_TITLES[-1])


class GrantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")

    def test_same_action_is_rewarded_once(self):
        award = xp.Award("lecture:guide:1", 30, Kind.READING, "Lecture")
        self.assertEqual(xp.grant(self.user, [award]).xp, 30)
        self.assertEqual(xp.grant(self.user, [award]).xp, 0)
        self.assertEqual(xp.total_xp(self.user), 30)

    def test_level_up_is_reported(self):
        xp.grant(self.user, [xp.Award("a", 90, Kind.BONUS, "a")])
        gain = xp.grant(self.user, [xp.Award("b", 20, Kind.BONUS, "b")])
        self.assertTrue(gain.level_up)
        self.assertEqual((gain.level_before, gain.level_after, gain.title), (1, 2, "Stagiaire SSI"))

    def test_streak_counts_days_with_enough_xp(self):
        today = timezone.localdate()
        totals = {
            today - timedelta(days=1): 15,
            today - timedelta(days=2): 10,
            today - timedelta(days=3): 5,
            today - timedelta(days=4): 40,
        }
        self.assertEqual(xp.streak(totals, today), (2, False))  # en danger : rien aujourd'hui
        totals[today] = 12
        self.assertEqual(xp.streak(totals, today), (3, True))
        self.assertEqual(xp.streak({today - timedelta(days=2): 50}, today), (0, False))

    def test_summary(self):
        now = timezone.now()
        xp.grant(
            self.user, [xp.Award("hier", 120, Kind.TEST, "Hier")], when=now - timedelta(days=1)
        )
        xp.grant(self.user, [xp.Award("ajd", 15, Kind.TEST, "Aujourd'hui")])
        summary = xp.summary(self.user)
        self.assertEqual(
            (summary.total, summary.level, summary.level_xp, summary.level_size), (135, 2, 35, 200)
        )
        self.assertEqual(
            (summary.today, summary.goal, summary.goal_left, summary.goal_reached),
            (15, 20, 5, False),
        )
        self.assertEqual((summary.streak, summary.streak_today), (2, True))
        self.assertEqual(summary.week_total, 135)


class PageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)

    def test_header_shows_the_xp_bar(self):
        xp.grant(self.user, [xp.Award("a", 40, Kind.BONUS, "a")])
        response = self.client.get(reverse("catalogue:dashboard"))
        self.assertContains(response, "Niv. 1")
        self.assertContains(response, "40 / 100 XP")

    def test_progression_page(self):
        xp.grant(self.user, [xp.Award("a", 25, Kind.READING, "Lecture terminée : Guide TLS")])
        response = self.client.get(reverse("progression:overview"))
        self.assertContains(response, "Lecture terminée : Guide TLS")
        self.assertContains(response, "+25")

    def test_daily_goal_can_be_changed(self):
        self.client.post(reverse("progression:set_goal"), {"daily_goal": "50"})
        self.assertEqual(LearnerProfile.objects.get(user=self.user).daily_goal, 50)
        self.client.post(
            reverse("progression:set_goal"), {"daily_goal": "7"}
        )  # valeur hors liste ignorée
        self.assertEqual(LearnerProfile.objects.get(user=self.user).daily_goal, 50)

    def test_goal_redirect_stays_on_site(self):
        response = self.client.post(reverse("progression:set_goal"),
                                    {"daily_goal": "20", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("progression:overview"))
