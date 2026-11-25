"""Conformité : attribution des publications de l'ANSSI, marques des certifications tierces, pages
légales."""

import datetime

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from .models import Certification, Guide, Referential


class ComplianceTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("lecteur"))

    def test_anssi_guide_cites_source_and_licence(self):
        guide = Guide.objects.create(
            title="Guide d'hygiène informatique",
            slug="hygiene",
            anssi_id="hygiene",
            version="2.0",
            published_on=datetime.date(2017, 9, 1),
            source_url="https://cyber.gouv.fr/publications/guide-dhygiene-informatique",
        )
        page = self.client.get(guide.get_absolute_url())
        self.assertContains(page, "Source : ANSSI")
        self.assertContains(page, "Licence Ouverte v2.0")
        self.assertContains(page, "septembre 2017")
        internal = Guide.objects.create(title="Charte interne", slug="charte")
        self.assertNotContains(self.client.get(internal.get_absolute_url()), "Source : ANSSI")

    def test_third_party_exam_outline_notice_and_switch(self):
        outline = Referential.objects.create(
            name="Programme d'examen CISSP",
            short_name="CISSP",
            slug="programme-cissp",
            kind=Referential.Kind.EXAM,
            issuer="ISC2",
            source_url="https://www.isc2.org/certifications/cissp",
        )
        page = self.client.get(outline.get_absolute_url())
        self.assertContains(page, "est une marque de ISC2")
        self.assertContains(self.client.get(reverse("catalogue:exam_list")), "programme-cissp")
        with override_settings(THIRD_PARTY_EXAM_OUTLINES=False):
            self.assertEqual(self.client.get(outline.get_absolute_url()).status_code, 404)
            self.assertNotContains(
                self.client.get(reverse("catalogue:exam_list")), "programme-cissp"
            )

    def test_certification_trademark_notice(self):
        certification = Certification.objects.create(
            name="Certified Information Systems Security Professional",
            acronym="CISSP",
            slug="isc2-cissp",
            kind="individual",
            issuer="ISC2",
        )
        self.assertContains(self.client.get(certification.get_absolute_url()), "n'est ni affilié")

    @override_settings(
        INSTANCE_NAME="Académie Cyber",
        ORGANISATION_NAME="Exemple SAS",
        CONTACT_EMAIL="contact@example.org",
    )
    def test_legal_pages_are_public_and_use_instance_settings(self):
        self.client.logout()
        for name in ("legal_mentions", "legal_privacy", "legal_terms", "legal_credits"):
            page = self.client.get(reverse(name))
            self.assertEqual(page.status_code, 200, name)
            self.assertContains(page, "Académie Cyber")
        self.assertContains(self.client.get(reverse("legal_mentions")), "Exemple SAS")
        self.assertContains(self.client.get(reverse("accounts:login")), "Mentions légales")
