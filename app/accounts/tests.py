"""Sécurité des comptes : double authentification, inactivité, journal d'audit, limites, RGPD,
droits par rôle."""

import json
import time
from unittest import mock

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.checks import run_checks
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from catalogue import views as catalogue_views
from catalogue.models import AssistantQuestion

from . import totp
from .middleware import LAST_SEEN
from .models import AUTHORS_GROUP, CONTENT_MANAGERS_GROUP, AuditEvent, TwoFactor, User

PASSWORD = "Un-mot-de-passe-solide-42"


def author(username="redactrice"):
    user = User.objects.create_user(username, password=PASSWORD)
    user.groups.add(Group.objects.get_or_create(name=AUTHORS_GROUP)[0])
    return user


def current_code(user):
    return totp.code_at(TwoFactor.objects.get(user=user).secret, totp.counter_now())


@override_settings(MFA_REQUIRED_FOR_PRIVILEGED=True)
class TwoFactorTests(TestCase):
    def setUp(self):
        self.user = author()
        self.client.force_login(self.user)

    def activate(self):
        self.client.get(reverse("accounts:two_factor_setup"))
        response = self.client.post(
            reverse("accounts:two_factor_setup"), {"code": current_code(self.user)}
        )
        return response.context["codes"]

    def test_privileged_account_must_activate_first(self):
        response = self.client.get(reverse("catalogue:dashboard"))
        self.assertRedirects(
            response,
            reverse("accounts:two_factor_setup") + "?next=%2F",
            fetch_redirect_response=False,
        )
        page = self.client.get(reverse("accounts:two_factor_setup"))
        self.assertContains(page, "data:image/svg+xml")  # QR code
        self.assertEqual(
            self.client.post(reverse("accounts:two_factor_setup"), {"code": "000000"}).status_code,
            200,
        )
        codes = self.activate()
        self.assertEqual(len(codes), 10)
        self.assertEqual(self.client.get(reverse("catalogue:dashboard")).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="2fa_activee", user=self.user).exists())

    def test_new_session_asks_for_code_once_and_refuses_replay(self):
        self.activate()
        other = self.client_class()
        other.force_login(self.user)
        self.assertRedirects(
            other.get(reverse("studio:home")),
            reverse("accounts:two_factor_verify") + "?next=%2Fatelier%2F",
            fetch_redirect_response=False,
        )
        # Le code vient d'être utilisé pour l'activation : il ne sert pas une seconde fois.
        replay = other.post(
            reverse("accounts:two_factor_verify"), {"code": current_code(self.user)}
        )
        self.assertContains(replay, "Code incorrect")
        with mock.patch("accounts.totp.time.time", return_value=time.time() + totp.STEP):
            ok = other.post(
                reverse("accounts:two_factor_verify") + "?next=/atelier/",
                {"code": current_code(self.user)},
            )
            self.assertRedirects(ok, "/atelier/", fetch_redirect_response=False)
            self.assertEqual(other.get(reverse("studio:home")).status_code, 200)

    def test_recovery_code_works_once(self):
        codes = self.activate()
        for expected in (True, False):
            client = self.client_class()
            client.force_login(self.user)
            response = client.post(
                reverse("accounts:two_factor_verify"), {"code": codes[0].upper()}
            )
            self.assertEqual(response.status_code == 302, expected)
        self.assertEqual(len(TwoFactor.objects.get(user=self.user).recovery_codes), 9)

    def test_too_many_failures_log_out(self):
        self.activate()
        client = self.client_class()
        client.force_login(self.user)
        for _ in range(5):
            response = client.post(reverse("accounts:two_factor_verify"), {"code": "123456"})
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", client.session)
        self.assertEqual(AuditEvent.objects.filter(action="2fa_echec").count(), 5)

    def test_reconnecting_does_not_give_more_attempts(self):
        self.activate()
        for _ in range(
            11
        ):  # 5 échecs déconnectent ; au-delà de 10 essais, le compte est bloqué un quart d'heure
            client = self.client_class()
            client.force_login(self.user)
            response = client.post(reverse("accounts:two_factor_verify"), {"code": "123456"})
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        with mock.patch("accounts.totp.time.time", return_value=time.time() + totp.STEP):
            client = self.client_class()
            client.force_login(self.user)
            client.post(reverse("accounts:two_factor_verify"), {"code": current_code(self.user)})
            self.assertRedirects(
                client.get(reverse("studio:home")),
                reverse("accounts:login") + "?next=%2Fatelier%2F",
                fetch_redirect_response=False,
            )

    def test_activation_applies_to_sessions_already_open(self):
        learner = User.objects.create_user("apprenant", password=PASSWORD)
        other = self.client_class()
        for client in (self.client, other):
            client.force_login(learner)
            self.assertEqual(client.get(reverse("catalogue:dashboard")).status_code, 200)
        self.client.get(reverse("accounts:two_factor_setup"))
        self.client.post(reverse("accounts:two_factor_setup"), {"code": current_code(learner)})
        self.assertRedirects(
            other.get(reverse("catalogue:dashboard")),
            reverse("accounts:two_factor_verify") + "?next=%2F",
            fetch_redirect_response=False,
        )

    def test_learner_is_not_forced_but_can_opt_in(self):
        learner = User.objects.create_user("apprenant", password=PASSWORD)
        self.client.force_login(learner)
        self.assertEqual(self.client.get(reverse("catalogue:dashboard")).status_code, 200)
        self.assertContains(self.client.get(reverse("accounts:security")), "Activer")

    def test_mandatory_two_factor_cannot_be_disabled(self):
        self.activate()
        response = self.client.post(
            reverse("accounts:two_factor_disable"),
            {"password": PASSWORD, "code": current_code(self.user)},
            follow=True,
        )
        self.assertContains(response, "obligatoire pour votre rôle")
        self.assertTrue(
            TwoFactor.objects.filter(user=self.user, confirmed_at__isnull=False).exists()
        )

    def test_json_requests_get_403_before_second_factor(self):
        response = self.client.post(
            reverse("studio:ai_stream"), "{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)


class SessionAndHeadersTests(TestCase):
    def test_privileged_session_expires_after_inactivity(self):
        user = author()
        self.client.force_login(user)
        self.client.get(reverse("catalogue:dashboard"))
        session = self.client.session
        session[LAST_SEEN] = time.time() - 31 * 60
        session.save()
        response = self.client.get(reverse("studio:home"))
        self.assertRedirects(
            response,
            reverse("accounts:login") + "?next=%2Fatelier%2F",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="deconnexion_inactivite", user=user).exists()
        )

    def test_security_headers(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertIn("camera=()", response["Permissions-Policy"])

    def test_role_cache_follows_changes(self):
        user = User.objects.create_user("lecteur")
        self.assertFalse(user.is_author)
        user.groups.add(Group.objects.get_or_create(name=AUTHORS_GROUP)[0])
        self.assertTrue(user.is_author)
        user.groups.clear()
        self.assertFalse(user.is_author)


class AuditTests(TestCase):
    def test_login_and_failed_login_are_recorded(self):
        User.objects.create_user("alice", password=PASSWORD)
        self.client.post(reverse("accounts:login"), {"username": "alice", "password": "faux"})
        self.client.post(reverse("accounts:login"), {"username": "alice", "password": PASSWORD})
        actions = list(AuditEvent.objects.order_by("at").values_list("action", "username"))
        self.assertEqual(actions, [("connexion_echec", "alice"), ("connexion", "alice")])

    def test_role_change_is_recorded(self):
        admin = User.objects.create_superuser("admin", password=PASSWORD)
        target = User.objects.create_user("bob")
        self.client.force_login(admin)
        self.client.post(
            reverse("accounts:user_action", args=[target.pk]), {"action": "donner-redacteur"}
        )
        event = AuditEvent.objects.get(action="role_donne")
        self.assertEqual(
            (event.username, event.target, event.detail["role"]), ("admin", "bob", "Rédacteur")
        )

    def test_editorial_actions_are_recorded(self):
        manager = User.objects.create_user("gestion", is_staff=True)
        manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.client.force_login(manager)
        self.client.post(reverse("catalogue:generation_action", args=["jeu"]), {"action": "pause"})
        self.assertTrue(AuditEvent.objects.filter(action="generation", username="gestion").exists())


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_search_is_limited_per_user(self):
        self.client.force_login(User.objects.create_user("curieux"))
        for _ in range(30):
            self.assertEqual(
                self.client.get(reverse("catalogue:search"), {"q": "pare-feu"}).status_code, 200
            )
        self.assertEqual(
            self.client.get(reverse("catalogue:search"), {"q": "pare-feu"}).status_code, 429
        )


class RangeTests(TestCase):
    def test_open_ended_range_is_capped(self):
        data = b"x" * (catalogue_views.RANGE_CHUNK + 1000)
        request = RequestFactory().get("/", HTTP_RANGE="bytes=0-")
        response = catalogue_views._serve_range(
            request, SimpleUploadedFile("a.mp3", data), "audio/mpeg"
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), catalogue_views.RANGE_CHUNK)
        self.assertEqual(
            response["Content-Range"], f"bytes 0-{catalogue_views.RANGE_CHUNK - 1}/{len(data)}"
        )


class PersonalDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lea", email="lea@example.org", password=PASSWORD)
        AssistantQuestion.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_export_contains_personal_data_without_secrets(self):
        TwoFactor.objects.create(
            user=self.user, secret="SECRETSECRETSECRET", recovery_codes=["abc"]
        )
        response = self.client.post(reverse("accounts:export_data"))
        self.assertIn("attachment", response["Content-Disposition"])
        data = json.loads(response.content)
        self.assertEqual(data["compte"]["e-mail"], "lea@example.org")
        self.assertEqual(len(data["catalogue.assistantquestion"]), 1)
        self.assertNotIn("SECRETSECRETSECRET", response.content.decode())
        self.assertNotIn("password", response.content.decode())

    def test_delete_account(self):
        refused = self.client.post(
            reverse("accounts:delete_account"), {"password": "faux", "confirm": "lea"}, follow=True
        )
        self.assertContains(refused, "pseudo de confirmation incorrect")
        self.client.post(
            reverse("accounts:delete_account"), {"password": PASSWORD, "confirm": "lea"}
        )
        self.assertFalse(User.objects.filter(username="lea").exists())
        self.assertFalse(AssistantQuestion.objects.exists())
        self.assertTrue(
            AuditEvent.objects.filter(action="compte_supprime", username="lea").exists()
        )

    def test_privileged_account_cannot_delete_itself(self):
        writer = author("auteur")
        self.client.force_login(writer)
        self.client.post(
            reverse("accounts:delete_account"), {"password": PASSWORD, "confirm": "auteur"}
        )
        self.assertTrue(User.objects.filter(username="auteur").exists())


class DeployCheckTests(TestCase):
    def test_weak_secret_key_blocks_deploy(self):
        with override_settings(SECRET_KEY="court"):
            ids = [m.id for m in run_checks(include_deployment_checks=True)]
        self.assertIn("accounts.E001", ids)
        with override_settings(SECRET_KEY="k" * 20 + "0123456789abcdefghijklmnopqrstuvwxyz"):
            ids = [m.id for m in run_checks(include_deployment_checks=True)]
        self.assertNotIn("accounts.E001", ids)


class PermissionMatrixTests(TestCase):
    """Pages réservées : un visiteur est renvoyé à la connexion, un apprenant reçoit un refus."""
    RESERVED = [
        ("studio:home", []),
        ("studio:list", ["cours"]),
        ("studio:create", ["cours"]),
        ("catalogue:review_list", []),
        ("catalogue:generation_dashboard", []),
        ("catalogue:generation_queue", ["audio"]),
        ("courses:review_list", []),
        ("accounts:user_list", []),
    ]

    def test_reserved_pages(self):
        learner = User.objects.create_user("apprenti")
        for name, args in self.RESERVED:
            url = reverse(name, args=args)
            self.client.logout()
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertTrue(response["Location"].startswith(reverse("accounts:login")), url)
            self.client.force_login(learner)
            self.assertIn(self.client.get(url).status_code, (302, 403, 404), url)
            self.assertNotEqual(self.client.get(url).status_code, 200, url)


class InvitationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", password=PASSWORD)
        self.client.force_login(self.admin)

    def follow_link(self, link, password="Une-autre-phrase-tres-longue-7"):  # gitleaks:allow
        path = link.split("testserver", 1)[1]
        page = self.client.get(
            path, follow=True
        )  # Django remplace le jeton par « set-password » dans l'URL
        self.assertContains(page, "Choisir votre mot de passe")
        return self.client.post(
            page.redirect_chain[-1][0], {"new_password1": password, "new_password2": password}
        )

    def test_create_account_without_email_shows_link(self):
        page = self.client.post(
            reverse("accounts:user_create"), {"username": "nouvelle", "role": "redacteur"}
        )
        self.assertContains(page, "Transmettez ce lien")
        self.assertEqual(page["Cache-Control"], "no-store")
        user = User.objects.get(username="nouvelle")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_author)
        self.client.logout()
        self.assertRedirects(
            self.follow_link(page.context["link"]), reverse("accounts:password_reset_complete")
        )
        login = self.client.post(
            reverse("accounts:login"),
            {
                "username": "nouvelle",
                "password": "Une-autre-phrase-tres-longue-7",  # gitleaks:allow
            },
        )
        self.assertRedirects(login, reverse("catalogue:dashboard"), fetch_redirect_response=False)
        self.assertTrue(AuditEvent.objects.filter(action="compte_cree", target="nouvelle").exists())

    @override_settings(
        EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
    )
    def test_invitation_email_and_single_use(self):
        from django.core import mail
        page = self.client.post(
            reverse("accounts:user_create"), {"username": "invite", "email": "invite@example.org"}
        )
        self.assertContains(page, "Le lien a été envoyé")
        self.assertEqual(mail.outbox[0].to, ["invite@example.org"])
        link = page.context["link"]
        self.assertIn(link, mail.outbox[0].body)
        self.client.logout()
        self.follow_link(link)
        reused = self.client.get(link.split("testserver", 1)[1], follow=True)
        self.assertContains(reused, "Lien expiré")

    @override_settings(
        EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
    )
    def test_forgotten_password(self):
        from django.core import mail
        User.objects.create_user("oubli", email="oubli@example.org", password=PASSWORD)
        self.client.logout()
        cache.clear()
        response = self.client.post(
            reverse("accounts:password_reset"), {"email": "oubli@example.org"}
        )
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertIn("/compte/nouveau-mot-de-passe/", mail.outbox[0].body)
        # Adresse inconnue : même réponse, aucun e-mail (on ne révèle pas quels comptes existent).
        self.client.post(reverse("accounts:password_reset"), {"email": "inconnu@example.org"})
        self.assertEqual(len(mail.outbox), 1)

    def test_only_admins_create_accounts(self):
        self.client.force_login(User.objects.create_user("simple"))
        self.assertEqual(
            self.client.post(reverse("accounts:user_create"), {"username": "x"}).status_code, 403
        )


class CreateAdminCommandTests(TestCase):
    def test_creates_admin_without_password_and_prints_link(self):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command(
            "creer_admin",
            "chef",
            "--url",
            "https://formation.exemple.fr/",
            "--email",
            "chef@exemple.fr",
            stdout=out,
        )
        link = out.getvalue().strip()
        self.assertTrue(
            link.startswith("https://formation.exemple.fr/compte/nouveau-mot-de-passe/")
        )
        admin = User.objects.get(username="chef")
        self.assertTrue(admin.is_superuser and admin.is_staff)
        self.assertFalse(admin.has_usable_password())
        self.assertEqual(self.client.get(link.split(".fr", 1)[1], follow=True).status_code, 200)
