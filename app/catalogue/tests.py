from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP

from .models import Guide, ReadingListItem, Referential
from .validators import validate_pdf, validate_xlsx

User = get_user_model()
STRONG_PASSWORD = "Un-mot-de-passe-solide-42!"


@override_settings(SIGNUP_ENABLED=True)  # inscriptions fermées par défaut : ces tests les ouvrent
class AccountTests(TestCase):
    def test_signup_creates_standard_user_and_logs_in(self):
        response = self.client.post(reverse("accounts:signup"), {
            "username": "alice", "email": "",
            "password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD,
        })
        self.assertRedirects(response, reverse("catalogue:dashboard"))
        user = User.objects.get(username="alice")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_signup_rejects_weak_password(self):
        response = self.client.post(reverse("accounts:signup"), {
            "username": "bob", "password1": "123456", "password2": "123456",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="bob").exists())

    def test_signup_username_is_case_insensitive_unique(self):
        User.objects.create_user("Alice", password=STRONG_PASSWORD)
        self.client.post(reverse("accounts:signup"), {
            "username": "alice", "password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD,
        })
        self.assertEqual(User.objects.filter(username__iexact="alice").count(), 1)

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_can_be_closed(self):
        self.assertEqual(self.client.get(reverse("accounts:signup")).status_code, 404)

    def test_password_is_hashed_with_argon2(self):
        user = User.objects.create_user("carol", password=STRONG_PASSWORD)
        self.assertTrue(user.password.startswith("argon2"))

    def test_content_managers_group_exists(self):
        group = Group.objects.get(name=CONTENT_MANAGERS_GROUP)
        self.assertTrue(group.permissions.filter(codename="change_guide").exists())
        self.assertFalse(group.permissions.filter(codename="change_user").exists())


class GuideTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password=STRONG_PASSWORD)
        self.guide = Guide.objects.create(title="Guide d'hygiène", slug="hygiene")
        self.hidden = Guide.objects.create(title="Brouillon", slug="brouillon", is_published=False)

    def test_pages_require_login(self):
        for name in ("catalogue:guide_list", "catalogue:certification_list"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response["Location"])

    def test_unpublished_guide_is_hidden(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("catalogue:guide_detail", args=["brouillon"]))
        self.assertEqual(response.status_code, 404)

    def test_finish_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("catalogue:reading_finish")).status_code, 405)

    def test_finish_is_unique_and_keeps_first_date(self):
        self.client.force_login(self.user)
        url = reverse("catalogue:reading_finish")
        self.client.post(url, {"kind": "guide", "slug": "hygiene"})
        first = ReadingListItem.objects.get(user=self.user, guide=self.guide).finished_at
        self.client.post(url, {"kind": "guide", "slug": "hygiene"})
        self.assertEqual(
            ReadingListItem.objects.filter(user=self.user, guide=self.guide).count(), 1
        )
        self.assertEqual(
            ReadingListItem.objects.get(user=self.user, guide=self.guide).finished_at, first
        )

    def test_database_enforces_uniqueness(self):
        ReadingListItem.objects.create(user=self.user, guide=self.guide)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReadingListItem.objects.create(user=self.user, guide=self.guide)

    def test_item_is_either_guide_or_referential(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReadingListItem.objects.create(user=self.user)


class PdfValidatorTests(TestCase):
    def test_accepts_real_pdf(self):
        validate_pdf(
            SimpleUploadedFile("guide.pdf", b"%PDF-1.7\n...", content_type="application/pdf")
        )

    def test_rejects_fake_pdf(self):
        with self.assertRaises(ValidationError):
            validate_pdf(
                SimpleUploadedFile("guide.pdf", b"<script>", content_type="application/pdf")
            )

    def test_rejects_wrong_extension(self):
        with self.assertRaises(ValidationError):
            validate_pdf(SimpleUploadedFile("guide.exe", b"%PDF-1.7"))

    def test_trame_must_be_a_real_xlsx(self):
        validate_xlsx(SimpleUploadedFile("trame.xlsx", b"PK\x03\x04..."))
        for name, content in (("trame.xlsx", b"<html>"), ("trame.html", b"PK\x03\x04...")):
            with self.assertRaises(ValidationError):
                validate_xlsx(SimpleUploadedFile(name, content))
        with self.assertRaises(ValidationError):
            Referential(
                name="R", slug="r", trame=SimpleUploadedFile("trame.xlsx", b"<html>")
            ).full_clean()
