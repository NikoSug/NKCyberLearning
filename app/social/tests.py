"""Amis : invitations, réponses, retrait et classement de la semaine."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from progression import xp
from progression.models import XPEvent

from . import friends
from .models import Friendship

User = get_user_model()
PASSWORD = "Un-mot-de-passe-solide-42!"


class FriendTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password=PASSWORD)
        self.bob = User.objects.create_user("Bob", password=PASSWORD)
        self.carol = User.objects.create_user("carol", password=PASSWORD)

    def test_invite_and_accept(self):
        target, outcome = friends.invite(self.alice, "bob")  # pseudo sans tenir compte de la casse
        self.assertEqual((target, outcome), (self.bob, "envoyee"))
        self.assertFalse(friends.are_friends(self.alice, self.bob))
        invitation = friends.incoming(self.bob)[0]
        friends.respond(self.bob, invitation.pk, accept=True)
        self.assertTrue(friends.are_friends(self.alice, self.bob))
        self.assertEqual(friends.friends(self.alice), [self.bob])
        self.assertEqual(friends.friends(self.bob), [self.alice])

    def test_crossed_invitations_become_friendship(self):
        friends.invite(self.alice, "Bob")
        _, outcome = friends.invite(self.bob, "alice")
        self.assertEqual(outcome, "acceptee")
        self.assertEqual(Friendship.objects.count(), 1)

    def test_invalid_invitations(self):
        for username, message in (("inconnu", "Aucun utilisateur"), ("alice", "vous-même")):
            with self.assertRaisesMessage(friends.FriendError, message):
                friends.invite(self.alice, username)
        friends.invite(self.alice, "Bob")
        with self.assertRaisesMessage(friends.FriendError, "déjà envoyée"):
            friends.invite(self.alice, "Bob")

    def test_only_the_addressee_can_answer(self):
        friends.invite(self.alice, "Bob")
        invitation = Friendship.objects.get()
        with self.assertRaises(friends.FriendError):
            friends.respond(self.carol, invitation.pk, accept=True)
        with self.assertRaises(friends.FriendError):
            friends.respond(self.alice, invitation.pk, accept=True)
        friends.respond(self.bob, invitation.pk, accept=False)
        self.assertFalse(Friendship.objects.exists())

    def test_cancel_and_remove(self):
        friends.invite(self.alice, "Bob")
        friends.cancel(self.alice, Friendship.objects.get().pk)
        self.assertFalse(Friendship.objects.exists())
        friends.invite(self.alice, "Bob")
        friends.invite(self.bob, "alice")
        friends.remove(self.bob, self.alice.pk)
        self.assertFalse(friends.are_friends(self.alice, self.bob))
        with self.assertRaises(friends.FriendError):
            friends.remove(self.bob, self.carol.pk)

    def test_pending_limit(self):
        for n in range(friends.MAX_PENDING):
            User.objects.create_user(f"user{n}", password=PASSWORD)
            friends.invite(self.alice, f"user{n}")
        with self.assertRaisesMessage(friends.FriendError, "Trop d'invitations"):
            friends.invite(self.alice, "carol")

    def test_weekly_leaderboard_only_shows_friends(self):
        friends.invite(self.alice, "Bob")
        friends.invite(self.bob, "alice")
        xp.grant(self.alice, [xp.Award("a", 30, XPEvent.Kind.BONUS, "a")])
        xp.grant(self.bob, [xp.Award("b", 50, XPEvent.Kind.BONUS, "b")])
        xp.grant(self.bob, [xp.Award("old", 500, XPEvent.Kind.BONUS, "old")],
                 when=timezone.now() - timedelta(days=8))  # semaine précédente
        xp.grant(self.carol, [xp.Award("c", 999, XPEvent.Kind.BONUS, "c")])  # pas amie
        rows = friends.leaderboard(self.alice)
        self.assertEqual(
            [(r.user.username, r.week_xp, r.rank) for r in rows], [("Bob", 50, 1), ("alice", 30, 2)]
        )
        self.assertEqual(rows[0].total, 550)
        self.assertTrue(rows[1].is_me)


class FriendPageTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password=PASSWORD)
        self.bob = User.objects.create_user("bob", password=PASSWORD)

    def test_full_flow_through_pages(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("social:invite"), {"username": "bob"}, follow=True)
        self.assertContains(response, "Invitation envoyée à bob")

        self.client.force_login(self.bob)
        page = self.client.get(reverse("catalogue:dashboard"))
        self.assertContains(page, 'class="nav-badge"')  # pastille d'invitation
        invitation = Friendship.objects.get()
        response = self.client.post(reverse("social:accept", args=[invitation.pk]), follow=True)
        self.assertContains(response, "alice et vous êtes maintenant amis")
        self.assertContains(response, "Classement de la semaine")

    def test_actions_require_post(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("social:invite")).status_code, 405)
