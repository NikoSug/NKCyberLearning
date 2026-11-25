"""Gains d'émeraudes : niveaux, objectif du jour, paliers de série."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import emeralds, xp
from .models import EmeraldEvent, XPEvent

User = get_user_model()


def give_xp(user, amount, ref, days_ago=0):
    return xp.grant(user, [xp.Award(ref, amount, XPEvent.Kind.BONUS, ref)],
                    when=timezone.now() - timedelta(days=days_ago) if days_ago else None)


class EmeraldTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")

    def refs(self, kind):
        return sorted(
            EmeraldEvent.objects.filter(user=self.user, kind=kind).values_list("ref", flat=True)
        )

    def test_each_new_level_pays_once(self):
        gain = give_xp(self.user, 320, "gros-gain")  # niveaux 2 et 3 d'un coup
        self.assertEqual(gain.level_after, 3)
        self.assertEqual(self.refs("niveau"), ["niveau:2", "niveau:3"])
        self.assertEqual(sum(e.amount for e in EmeraldEvent.objects.filter(kind="niveau")), 20 + 30)
        emeralds.after_xp(self.user, 1, 3)  # rejouer ne verse rien
        self.assertEqual(EmeraldEvent.objects.filter(kind="niveau").count(), 2)

    def test_daily_goal(self):
        give_xp(self.user, 15, "a")
        self.assertEqual(self.refs("objectif"), [])  # objectif par défaut : 20 XP
        gain = give_xp(self.user, 5, "b")
        self.assertEqual(gain.emeralds, emeralds.GOAL_REWARD)
        give_xp(self.user, 30, "c")
        self.assertEqual(len(self.refs("objectif")), 1)

    def test_streak_milestones_pay_once_per_streak(self):
        for days_ago in (2, 1):
            give_xp(self.user, 10, f"jour-{days_ago}", days_ago=days_ago)
        self.assertEqual(self.refs("serie"), [])
        give_xp(self.user, 10, "jour-0")
        self.assertEqual(len(self.refs("serie")), 1)  # palier de 3 jours
        give_xp(self.user, 10, "encore")
        self.assertEqual(len(self.refs("serie")), 1)

    def test_purchases_reduce_the_balance_only(self):
        emeralds.credit(self.user, "x", 50, EmeraldEvent.Kind.LEVEL, "x")
        emeralds.credit(self.user, "achat:y", -20, EmeraldEvent.Kind.PURCHASE, "y")
        self.assertEqual(emeralds.balance(self.user), 30)
        self.assertEqual(xp.total_xp(self.user), 0)
