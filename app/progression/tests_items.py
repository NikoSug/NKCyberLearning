"""Objets de la boutique : cosmétiques, gel de série, potion double XP."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import emeralds, items, shop, xp
from .models import EmeraldEvent, LearnerProfile, StreakFreezeUse, XPEvent

User = get_user_model()


def give_xp(user, amount, ref, days_ago=0):
    when = timezone.now() - timedelta(days=days_ago) if days_ago else None
    return xp.grant(user, [xp.Award(ref, amount, XPEvent.Kind.BONUS, ref)], when=when)


class ItemTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)

    def level_up(self, level):
        give_xp(self.user, xp.level_start(level), f"niveau-{level}", days_ago=30)
        emeralds.credit(self.user, "cadeau", 1000, EmeraldEvent.Kind.LEVEL, "Cadeau")

    def test_cosmetics_are_bought_once_equipped_and_level_gated(self):
        self.level_up(3)
        with self.assertRaisesMessage(shop.ShopError, "niveau 4"):
            shop.buy_item(self.user, "cadre-circuit")
        shop.buy_item(self.user, "cadre-or")
        shop.buy_item(self.user, "theme-terminal")
        shop.buy_item(self.user, "titre-chiffrement")
        summary = xp.summary(self.user)
        self.assertEqual((summary.frame.key, summary.theme.css, summary.profile_title.name),
                         ("cadre-or", "terminal", "Pro du chiffrement"))
        with self.assertRaisesMessage(shop.ShopError, "déjà"):
            shop.buy_item(self.user, "cadre-or")
        shop.equip_item(self.user, items.FRAME, "")
        self.assertIsNone(xp.summary(self.user).frame)
        with self.assertRaises(shop.ShopError):
            shop.equip_item(self.user, items.FRAME, "cadre-argent")  # pas acheté

    def test_theme_and_frame_are_rendered(self):
        self.level_up(3)
        shop.buy_item(self.user, "theme-audit")
        shop.buy_item(self.user, "cadre-argent")
        response = self.client.get(reverse("catalogue:dashboard"))
        self.assertContains(response, 'data-theme="audit"')
        self.assertContains(response, "avatar-frame frame-silver")

    def test_free_light_and_dark_themes_are_owned_from_the_start(self):
        # Niveau 1, aucune émeraude : clair et sombre sont offerts, il suffit de choisir.
        self.assertTrue({"theme-clair", "theme-sombre"} <= shop.owned_items(self.user))
        shop_page = self.client.get(reverse("progression:shop"))
        self.assertContains(shop_page, "swatch-clair")
        self.assertContains(shop_page, "swatch-sombre")
        for key, css in (("theme-clair", "clair"), ("theme-sombre", "sombre")):
            self.client.post(
                reverse("progression:equip_item"), {"category": items.THEME, "item": key}
            )
            self.assertContains(
                self.client.get(reverse("catalogue:dashboard")), f'data-theme="{css}"'
            )
        self.assertContains(
            self.client.get(reverse("progression:shop")), "Suivre le réglage de l'ordinateur"
        )
        self.client.post(reverse("progression:equip_item"), {"category": items.THEME, "item": ""})
        self.assertNotContains(
            self.client.get(reverse("catalogue:dashboard")), "data-theme="
        )  # suit l'ordinateur
        self.assertEqual(emeralds.balance(self.user), 0)
        with self.assertRaisesMessage(shop.ShopError, "déjà"):
            shop.buy_item(self.user, "theme-sombre")

    def test_double_xp_potion(self):
        self.level_up(4)
        before = xp.total_xp(self.user)
        shop.buy_item(self.user, items.DOUBLE_XP)
        gain = give_xp(self.user, 10, "potion")
        self.assertEqual((gain.xp, gain.doubled), (20, True))
        self.assertEqual(xp.total_xp(self.user), before + 20)
        with self.assertRaisesMessage(shop.ShopError, "déjà active"):
            shop.buy_item(self.user, items.DOUBLE_XP)
        LearnerProfile.objects.filter(user=self.user).update(
            double_xp_until=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(give_xp(self.user, 10, "apres").xp, 10)

    def test_streak_freeze_stock(self):
        self.level_up(2)
        shop.buy_item(self.user, items.STREAK_FREEZE)
        shop.buy_item(self.user, items.STREAK_FREEZE)
        with self.assertRaisesMessage(shop.ShopError, "réserve"):
            shop.buy_item(self.user, items.STREAK_FREEZE)
        self.assertEqual(xp.summary(self.user).freezes, 2)


class StreakFreezeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("bob", password="Un-mot-de-passe-solide-42!")
        self.today = timezone.localdate()
        self.profile = xp.profile(self.user)

    def totals(self, *days_ago):
        return {self.today - timedelta(days=d): 20 for d in days_ago}

    def test_a_missed_day_is_covered_by_a_freeze(self):
        self.profile.streak_freezes = 1
        self.profile.save()
        totals = self.totals(2, 3, 4)  # rien hier
        self.assertEqual(xp.streak_for(self.user, totals, self.today), (3, False))
        self.assertEqual(LearnerProfile.objects.get(user=self.user).streak_freezes, 0)
        self.assertTrue(
            StreakFreezeUse.objects.filter(date=self.today - timedelta(days=1)).exists()
        )
        self.assertEqual(
            xp.streak_for(self.user, totals, self.today), (3, False)
        )  # pas de seconde consommation

    def test_freezes_are_not_wasted(self):
        self.profile.streak_freezes = 1
        self.profile.save()
        xp.streak_for(self.user, self.totals(3, 4), self.today)  # 2 jours manqués, 1 seul gel
        xp.streak_for(self.user, {}, self.today)  # aucune série à sauver
        xp.streak_for(self.user, self.totals(1, 2), self.today)  # rien de manqué
        self.assertEqual(LearnerProfile.objects.get(user=self.user).streak_freezes, 1)
        self.assertFalse(StreakFreezeUse.objects.exists())

    def test_two_freezes_cover_two_days(self):
        self.profile.streak_freezes = 2
        self.profile.save()
        self.assertEqual(xp.streak_for(self.user, self.totals(3, 4, 5), self.today), (3, False))
        self.assertEqual(StreakFreezeUse.objects.count(), 2)
