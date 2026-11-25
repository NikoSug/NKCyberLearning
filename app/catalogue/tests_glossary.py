"""Lexique des guides (modèle simulé), relecture rassemblée, pause de toutes les générations."""

import shutil
import tempfile
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP
from courses.models import Course
from courses.templatetags.course_text import lesson_html

from . import generation, glossary, narration_text, quizgen
from .models import (
    GenerationControl,
    Glossary,
    GlossaryEntry,
    Guide,
    GuideDocument,
    QuizDraft,
    ServiceCommand,
)

User = get_user_model()
TEXT = (
    "Le système d'information (SI) est protégé par un pare-feu placé en DMZ. Un VPN chiffre les"
    " échanges. La PSSI fixe les règles. "
    * 30
)
SECTIONS = [narration_text.Section("Chapitre 1", TEXT)]


def fake_chat(messages, schema, temperature=0.2):
    return {
        "entrees": [
            {
                "terme": "SI",
                "type": "sigle",
                "forme_developpee": "Système d'information",
                "definition": "Ensemble des moyens informatiques d'une organisation.",
            },
            {
                "terme": "VPN",
                "type": "sigle",
                "forme_developpee": "",
                "definition": "Tunnel chiffré qui relie deux réseaux à travers Internet.",
            },
            {
                "terme": "Pare-feu",
                "type": "terme",
                "forme_developpee": "",
                "definition": "Équipement qui filtre les communications entre deux réseaux.",
            },
            {
                "terme": "XYZ",
                "type": "sigle",
                "forme_developpee": "Sigle inventé",
                "definition": "Ce sigle n'apparaît pas dans le texte du guide.",
            },
            {
                "terme": "R12",
                "type": "terme",
                "forme_developpee": "",
                "definition": "Une recommandation du guide en question.",
            },
            {
                "terme": "VM-",
                "type": "sigle",
                "forme_developpee": "",
                "definition": "Morceau de nom de produit capté par erreur.",
            },
        ]
    }


class GlossaryTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.guide = Guide.objects.create(title="Guide pare-feu", slug="pare-feu")
        document = GuideDocument(guide=self.guide, label="PDF")
        document.file.save("pare-feu.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)
        self.manager = User.objects.create_user("gestion", password="Un-mot-de-passe-solide-42!")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def generate(self):
        glossary.request(self.guide)
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(quizgen, "chat", side_effect=fake_chat):
            return glossary.generate(glossary.take_next())

    def test_generation_keeps_only_terms_present_in_the_guide(self):
        item = self.generate()
        self.assertEqual(item.status, Glossary.Status.REVIEW)
        self.assertEqual(
            sorted(item.entries.values_list("term", flat=True)), ["Pare-feu", "SI", "VPN"]
        )
        self.assertEqual(item.entries.get(term="SI").kind, GlossaryEntry.Kind.ACRONYM)
        self.assertEqual(
            glossary.acronym_candidates("Le VM-Series, TCP/IP, des VPNs, la PSSI, la PSSI."),
            ["PSSI", "TCP/IP", "VPN"],
        )
        self.assertEqual(list(glossary.guides_without_glossary()), [])

    def test_unreadable_answer_splits_the_part(self):
        calls = []

        def chat(messages, schema, temperature=0.2):
            calls.append(len(messages[1]["content"]))
            if len(calls) == 1:  # partie entière : réponse coupée (contexte du modèle plein)
                raise quizgen.UnreadableAnswer("réponse du modèle coupée")
            return fake_chat(messages, schema)
        glossary.request(self.guide)
        with mock.patch.object(
            narration_text, "extract_sections", return_value=SECTIONS
        ), mock.patch.object(quizgen, "chat", side_effect=chat):
            item = glossary.generate(glossary.take_next())
        self.assertEqual(item.status, Glossary.Status.REVIEW)
        self.assertEqual(len(calls), 3)  # puis chaque moitié
        self.assertLess(calls[1], calls[0])
        self.assertEqual(
            sorted(item.entries.values_list("term", flat=True)), ["Pare-feu", "SI", "VPN"]
        )

    def test_answer_still_unreadable_on_small_pieces_fails(self):
        glossary.request(self.guide)
        with mock.patch.object(
            narration_text, "extract_sections", return_value=SECTIONS
        ), mock.patch.object(
            quizgen, "chat", side_effect=quizgen.UnreadableAnswer("JSON invalide")
        ) as chat:
            with self.assertRaisesMessage(glossary.GenerationError, "JSON invalide"):
                glossary.generate(glossary.take_next())
        self.assertEqual(
            chat.call_count, 3
        )  # partie, première moitié, premier quart : toujours illisible → échec

    def test_review_publish_and_reading(self):
        item = self.generate()
        reader = reverse(
            "catalogue:guide_read", args=[self.guide.slug, self.guide.documents.first().pk]
        )
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reader), "📖 Lexique")  # brouillon : invisible
        entry = item.entries.get(term="VPN")
        self.client.post(
            reverse("catalogue:glossary_entry", args=[entry.pk]),
            {
                "term": "VPN",
                "kind": "sigle",
                "expansion": "Virtual Private Network (réseau privé virtuel)",
                "definition": "Tunnel chiffré entre deux réseaux, à travers Internet.",
            },
        )
        entry.refresh_from_db()
        self.assertTrue(entry.expansion.startswith("Virtual Private Network"))
        self.client.post(
            reverse("catalogue:glossary_review_action", args=[item.pk]),
            {
                "action": "ajouter",
                "term": "PSSI",
                "expansion": "Politique de sécurité des systèmes d'information",
                "definition": "Document qui fixe les règles de sécurité d'une organisation.",
            },
        )
        self.assertTrue(item.entries.filter(key="pssi").exists())
        self.client.post(
            reverse("catalogue:glossary_review_action", args=[item.pk]), {"action": "publier"}
        )
        self.assertEqual(Glossary.objects.get(pk=item.pk).status, Glossary.Status.PUBLISHED)

        response = self.client.get(reader)
        self.assertContains(response, "📖 Lexique")
        self.assertContains(response, "Politique de sécurité des systèmes d&#x27;information")
        index = self.client.get(reverse("catalogue:glossary_index"))
        self.assertContains(index, "Virtual Private Network")
        self.assertContains(
            self.client.get(reverse("catalogue:glossary_index"), {"q": "pssi"}), "PSSI"
        )

    def test_terms_are_marked_in_lessons(self):
        item = self.generate()
        entries = list(item.entries.all())
        html = lesson_html(
            "## Le SI\nUn SI bien géré utilise un VPN. Le SI encore.\n\n- Un pare-feu filtre.",
            entries,
        )
        self.assertEqual(
            html.count('data-term="si"'), 1
        )  # première apparition seulement, hors titre
        self.assertIn("<h2>Le SI</h2>", html)
        self.assertIn('data-term="vpn"', html)
        self.assertIn('data-term="pare-feu"', html)
        self.assertIn('<abbr class="term"', lesson_html("Le SI.", entries))
        self.assertNotIn(
            "<abbr", lesson_html("Le SIEM et le FAI.", entries)
        )  # pas de morceau de mot


class ReviewHubTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("gestion", password="Un-mot-de-passe-solide-42!")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.client.force_login(self.manager)
        guide = Guide.objects.create(title="Guide A", slug="a")
        QuizDraft.objects.create(guide=guide, status=QuizDraft.Status.REVIEW)
        Course.objects.create(kind=Course.Kind.GUIDE, guide=guide, title="Cours A", slug="guide-a",
                              status=Course.Status.REVIEW)
        Glossary.objects.create(guide=guide, status=Glossary.Status.REVIEW)

    def test_one_window_for_all_reviews(self):
        response = self.client.get(reverse("catalogue:review_list"))
        for text in ("QCM", "Cours A", "Lexique", "✍️ Relecture"):
            self.assertContains(response, text)
        self.assertEqual(len(response.context["to_review"]), 3)
        self.assertEqual(response.context["counts"]["tout"], 3)
        self.assertEqual(
            len(
                self.client.get(reverse("catalogue:review_list"), {"type": "cours"}).context[
                    "to_review"
                ]
            ),
            1,
        )
        self.assertRedirects(self.client.get(reverse("courses:review_list")),
                             reverse("catalogue:review_list") + "?type=cours")

    def test_start_services_through_the_host_agent(self):
        url = reverse("catalogue:generation_action", args=["services"])
        self.assertEqual(
            self.client.post(url, {"action": "lancer"}).status_code, 403
        )  # superutilisateur seulement
        admin = User.objects.create_superuser("admin", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(admin)
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")),
            "L'agent du poste ne répond pas",
        )
        self.client.post(url, {"action": "lancer"})
        self.assertFalse(ServiceCommand.objects.exists())  # agent absent : rien n'est demandé

        out = StringIO()
        call_command("service_requests", "--poll", stdout=out)  # l'agent se signale
        self.assertEqual(out.getvalue(), "")
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")), "Lancer les services"
        )
        self.client.post(url, {"action": "lancer"})
        self.client.post(url, {"action": "arreter"})  # une seule demande à la fois
        command = ServiceCommand.objects.get()
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")), "Lancement en cours"
        )
        call_command("service_requests", "--poll", stdout=out)
        self.assertEqual(out.getvalue().strip(), f"{command.pk} lancer")
        call_command(
            "service_requests",
            "--done",
            str(command.pk),
            "--error",
            "docker introuvable",
            stdout=out,
        )
        command.refresh_from_db()
        self.assertEqual(
            (command.status, command.message), (ServiceCommand.Status.FAILED, "docker introuvable")
        )
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")), "docker introuvable"
        )
        self.assertEqual(self.client.post(url, {"action": "autre"}).status_code, 404)

    def test_pause_all_generations(self):
        self.client.post(
            reverse("catalogue:generation_action", args=["toutes"]), {"action": "pause"}
        )
        self.assertEqual(generation.paused_count(), len(generation.BACKGROUND))
        self.assertTrue(generation.is_paused(GenerationControl.Kind.GLOSSARY))
        self.assertFalse(generation.gaming())  # l'assistant n'est pas coupé
        response = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(response, 'id="lexique"')
        self.assertContains(response, "Tout reprendre")
        self.client.post(
            reverse("catalogue:generation_action", args=["toutes"]), {"action": "reprendre"}
        )
        self.assertEqual(generation.paused_count(), 0)
