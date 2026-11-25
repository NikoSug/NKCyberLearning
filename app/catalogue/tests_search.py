"""Recherche globale (guides, texte complet, cours, podcasts, lexique) et filtre « version audio »
du catalogue."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course, Lesson
from podcasts.models import Episode, Series

from . import search
from .models import Glossary, GlossaryEntry, Guide, GuideCategory, GuideSection, Narration
from .templatetags.search_text import highlight

User = get_user_model()


class SearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)
        self.category = GuideCategory.objects.create(name="Réseau", slug="reseau")
        self.firewall = Guide.objects.create(
            title="Pare-feu et filtrage",
            slug="pare-feu",
            category=self.category,
            summary="Filtrer les flux réseau.",
        )
        self.backup = Guide.objects.create(title="Sauvegarde des systèmes", slug="sauvegarde",
                                           summary="Protéger ses données.")
        GuideSection.objects.create(
            guide=self.backup,
            position=1,
            title="La règle 3-2-1",
            text="Introduction. " * 40
            + "Une copie hors ligne protège contre un rançongiciel. "
            + "Conclusion. " * 40,
        )
        glossary = Glossary.objects.create(guide=self.firewall, status=Glossary.Status.PUBLISHED,
                                           published_at=timezone.now())
        GlossaryEntry.objects.create(
            glossary=glossary,
            term="EDR",
            key="edr",
            kind=GlossaryEntry.Kind.ACRONYM,
            expansion="Endpoint Detection and Response",
            definition="Outil qui détecte les attaques sur les postes.",
        )
        GlossaryEntry.objects.create(
            glossary=glossary,
            term="XDR",
            key="xdr",
            kind=GlossaryEntry.Kind.ACRONYM,
            definition="Extension de l'EDR à tout le système d'information.",
        )

    def get(self, **params):
        return self.client.get(reverse("catalogue:search"), params)

    def test_full_text_of_guides_gives_an_excerpt(self):
        response = self.get(q="rancongiciel")  # sans accent
        hits = response.context["results"]["texte"]
        self.assertEqual([h.obj for h in hits], [self.backup])
        self.assertIn("Chapitre : La règle 3-2-1", hits[0].meta)
        self.assertIn("rançongiciel", hits[0].excerpt)
        self.assertLess(len(hits[0].excerpt), 300)
        self.assertContains(response, "<mark>rançongiciel</mark>")

    def test_glossary_exact_term_first(self):
        entries = self.get(q="edr").context["results"]["lexique"]
        self.assertEqual([e["entry"].term for e in entries], ["EDR", "XDR"])
        self.assertEqual(entries[0]["guides"], [self.firewall])

    def test_tabs_and_title_relevance(self):
        Guide.objects.create(
            title="Journalisation", slug="journaux", summary="Garder la trace des flux de pare-feu."
        )
        response = self.get(q="pare-feu")
        self.assertEqual(
            response.context["results"]["guides"][0], self.firewall
        )  # titre avant résumé
        response = self.get(q="pare-feu", type="lexique")
        self.assertEqual(list(response.context["results"]), ["lexique"])
        self.assertEqual(response.context["counts"]["guides"], 2)  # les compteurs restent complets

    def test_courses_and_podcasts_are_found_once_published(self):
        course = Course.objects.create(
            kind=Course.Kind.THEME,
            category=self.category,
            title="Les bases du réseau",
            slug="theme-reseau",
            status=Course.Status.REVIEW,
        )
        Lesson.objects.create(
            course=course, position=1, title="Le filtrage", body="Un pare-feu filtre les flux."
        )
        series = Series.objects.create(
            category=self.category,
            title="Réseau en clair",
            slug="podcast-reseau",
            status=Series.Status.REVIEW,
        )
        Episode.objects.create(
            series=series, position=1, title="Pourquoi filtrer ?", summary="Le pare-feu expliqué."
        )
        results = self.get(q="pare-feu").context["results"]
        self.assertEqual(
            (results["cours"], results["podcasts"]), ([], [])
        )  # en relecture : invisibles
        Course.objects.filter(pk=course.pk).update(status=Course.Status.PUBLISHED)
        Series.objects.filter(pk=series.pk).update(status=Series.Status.PUBLISHED)
        results = self.get(q="pare-feu").context["results"]
        self.assertIn("Leçon 1 : Le filtrage", results["cours"][0].meta)
        self.assertIn("Épisode 1 : Pourquoi filtrer ?", results["podcasts"][0].meta)
        self.assertEqual(
            self.get(q="pare-feu", domaine="reseau", niveau="3-avance").context["results"]["cours"],
            [],
        )

    def test_audio_filter_and_badge(self):
        Narration.objects.create(guide=self.backup, status=Narration.Status.READY)
        Narration.objects.create(
            guide=self.firewall, status=Narration.Status.PENDING
        )  # pas encore prête
        listing = self.client.get(reverse("catalogue:guide_list"), {"audio": "avec"})
        self.assertEqual(listing.context["guides"], [self.backup])
        self.assertContains(listing, reverse("catalogue:guide_listen", args=["sauvegarde"]))
        without = self.client.get(reverse("catalogue:guide_list"), {"audio": "sans"})
        self.assertEqual(without.context["guides"], [self.firewall])
        self.assertContains(
            self.client.get(reverse("catalogue:path_list"), {"audio": "avec"}),
            'name="audio" value="avec"',
        )
        self.assertEqual(self.get(q="sauvegarde pare-feu filtrage systemes", format="audio")
                         .context["results"]["guides"], [])
        self.assertEqual(
            self.get(q="sauvegarde", format="audio").context["results"]["guides"], [self.backup]
        )

    def test_short_query_shows_help(self):
        response = self.get(q="a")
        self.assertNotIn("results", response.context)


class HighlightTests(TestCase):
    def test_accents_case_and_escaping(self):
        self.assertEqual(highlight("Sécurité <b>réseau</b>", ["securite"]),
                         "<mark>Sécurité</mark> &lt;b&gt;réseau&lt;/b&gt;")
        self.assertEqual(highlight("Pare-feu", []), "Pare-feu")

    def test_snippet_is_centred_on_the_word(self):
        text = "début " * 100 + "chiffrement fort " + "fin " * 100
        excerpt = search.snippet(text, ["chiffrement"])
        self.assertTrue(excerpt.startswith("…") and excerpt.endswith("…"))
        self.assertIn("chiffrement", excerpt)
