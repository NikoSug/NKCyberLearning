"""Boutique d'avatars : paiement en émeraudes, niveau minimum, choix de l'avatar."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import avatars, emeralds, shop, xp
from .models import AvatarPurchase, EmeraldEvent, LearnerProfile, XPEvent

User = get_user_model()


def give_xp(user, amount, ref="bonus"):
    xp.grant(user, [xp.Award(ref, amount, XPEvent.Kind.BONUS, "Bonus")])


def give_emeralds(user, amount, ref="cadeau"):
    emeralds.credit(user, ref, amount, EmeraldEvent.Kind.LEVEL, "Cadeau")


class ShopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)
        self.common = next(a for a in avatars.AVATARS if a.rarity == avatars.COMMON)
        self.rare = next(a for a in avatars.AVATARS if a.rarity == avatars.RARE)
        give_xp(
            self.user, xp.level_start(avatars.COMMON.min_level)
        )  # niveau 2 (et 20 émeraudes de niveau)

    def test_buying_spends_emeralds_and_never_xp(self):
        before = xp.summary(self.user)
        give_emeralds(self.user, 10)
        shop.buy(self.user, self.common.key)
        after = xp.summary(self.user)
        self.assertEqual((after.total, after.level), (before.total, before.level))  # XP intact
        self.assertEqual(after.emeralds, before.emeralds + 10 - self.common.price)
        self.assertEqual(after.avatar, self.common)
        self.assertEqual(EmeraldEvent.objects.get(kind="achat").amount, -self.common.price)

    def test_cannot_buy_without_enough_emeralds_or_twice(self):
        EmeraldEvent.objects.filter(user=self.user).delete()
        give_emeralds(self.user, self.common.price - 1)
        with self.assertRaisesMessage(shop.ShopError, "Il vous manque 1 émeraude"):
            shop.buy(self.user, self.common.key)
        give_emeralds(self.user, 50, ref="encore")
        shop.buy(self.user, self.common.key)
        with self.assertRaisesMessage(shop.ShopError, "déjà"):
            shop.buy(self.user, self.common.key)
        self.assertEqual(AvatarPurchase.objects.count(), 1)

    def test_rewards_are_unlocked_by_level(self):
        give_emeralds(self.user, 1000)
        self.assertEqual(xp.summary(self.user).level, 2)
        with self.assertRaisesMessage(shop.ShopError, f"niveau {avatars.RARE.min_level}"):
            shop.buy(self.user, self.rare.key)
        give_xp(self.user, xp.level_start(avatars.RARE.min_level), ref="niveau-3")
        shop.buy(self.user, self.rare.key)

    def test_an_avatar_can_require_more_than_its_rarity(self):
        special = avatars.Avatar("test", "Test", "🧪", "blue", avatars.COMMON, level=4)
        self.assertEqual(special.min_level, 4)
        give_emeralds(self.user, 100)
        with mock.patch.dict(avatars.BY_KEY, {special.key: special}):
            with self.assertRaisesMessage(shop.ShopError, "niveau 4"):
                shop.buy(self.user, special.key)

    def test_unknown_avatar(self):
        with self.assertRaises(shop.ShopError):
            shop.buy(self.user, "inexistant")

    def test_free_avatars_can_be_equipped_and_others_cannot(self):
        free = next(a for a in avatars.AVATARS if a.price == 0)
        shop.equip(self.user, free.key)
        self.assertEqual(LearnerProfile.objects.get(user=self.user).avatar, free.key)
        with self.assertRaises(shop.ShopError):
            shop.equip(self.user, self.common.key)
        shop.equip(self.user, "")  # retour à l'initiale
        self.assertEqual(LearnerProfile.objects.get(user=self.user).avatar, "")

    def test_shop_pages(self):
        balance = emeralds.balance(self.user)
        response = self.client.get(reverse("progression:shop"))
        self.assertContains(response, "Mes émeraudes")
        self.assertContains(response, f"<span>{balance}</span>")  # pastille de l'en-tête
        response = self.client.post(
            reverse("progression:buy_avatar"), {"avatar": self.common.key}, follow=True
        )
        self.assertContains(response, f"« {self.common.name} » est à vous")
        self.assertContains(response, f'aria-label="Avatar {self.common.name}"')
        response = self.client.post(
            reverse("progression:buy_avatar"), {"avatar": self.rare.key}, follow=True
        )
        self.assertContains(response, "flash-error")
