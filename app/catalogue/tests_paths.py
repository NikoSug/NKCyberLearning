"""Parcours de lecture par thème et par niveau."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import paths, reading
from .models import Guide, GuideCategory, Language, Level, ReadingListItem

User = get_user_model()


class PathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)
        self.basics = GuideCategory.objects.create(
            name="Fondamentaux", slug="fondamentaux", position=0
        )
        self.network = GuideCategory.objects.create(name="Réseau", slug="reseau", position=1)

        def make(slug, level, minutes, category=self.network, **kw):
            return Guide.objects.create(
                title=slug.upper(),
                slug=slug,
                level=level,
                estimated_minutes=minutes,
                category=category,
                **kw
            )
        self.long_beginner = make("pare-feu", Level.BEGINNER, 60)
        self.short_beginner = make("essentiels", Level.BEGINNER, 10)
        self.advanced = make("bgp", Level.ADVANCED, 90)
        self.intermediate = make("dns", Level.INTERMEDIATE, 30)
        make("english", Level.BEGINNER, 5, language=Language.EN)
        make("hygiene", Level.BEGINNER, 20, category=self.basics)

    def network_path(self):
        return self.client.get(reverse("catalogue:path_detail", args=["reseau"])).context["path"]

    def test_reading_order_goes_from_beginner_to_advanced_and_short_to_long(self):
        path = self.network_path()
        self.assertEqual(
            [g.slug for g in path.guides], ["essentiels", "pare-feu", "dns", "bgp"]
        )  # pas l'anglais
        self.assertEqual([s.label for s in path.stages], ["Débutant", "Intermédiaire", "Avancé"])
        self.assertEqual(path.minutes, 190)

    def test_list_follows_theme_order_and_filters(self):
        response = self.client.get(reverse("catalogue:path_list"))
        self.assertEqual(
            [p.category.slug for p in response.context["paths"]], ["fondamentaux", "reseau"]
        )
        response = self.client.get(reverse("catalogue:path_list"), {"niveau": Level.ADVANCED})
        self.assertEqual([[g.slug for g in p.guides] for p in response.context["paths"]], [["bgp"]])
        response = self.client.get(reverse("catalogue:path_list"), {"domaine": "fondamentaux"})
        self.assertEqual([p.category.slug for p in response.context["paths"]], ["fondamentaux"])

    def test_adding_a_path_keeps_its_order(self):
        reading.add(self.user, self.intermediate)  # déjà dans la liste : pas de doublon
        self.client.post(reverse("catalogue:reading_add_path", args=["reseau"]))
        order = list(ReadingListItem.objects.filter(user=self.user).order_by("position")
                     .values_list("guide__slug", flat=True))
        self.assertEqual(order, ["dns", "essentiels", "pare-feu", "bgp"])
        response = self.client.post(
            reverse("catalogue:reading_add_path", args=["reseau"]), follow=True
        )
        self.assertContains(response, "déjà tous dans votre lecture")

    def test_adding_a_single_level(self):
        self.client.post(
            reverse("catalogue:reading_add_path", args=["reseau"]), {"niveau": Level.BEGINNER}
        )
        self.assertEqual(
            set(ReadingListItem.objects.values_list("guide__slug", flat=True)),
            {"essentiels", "pare-feu"},
        )

    def test_progress_and_next_step(self):
        reading.mark_finished(self.user, self.short_beginner)
        reading.add(self.user, self.advanced)
        path = self.network_path()
        self.assertEqual((path.read, path.total, path.percent), (1, 4, 25))
        self.assertEqual(path.next_guide, self.long_beginner)
        self.assertTrue(path.started)
        built = paths.build(Guide.objects.none())
        self.assertEqual(built, [])
        response = self.client.get(reverse("catalogue:dashboard"))
        self.assertEqual([p.category.slug for p in response.context["paths_current"]], ["reseau"])
        self.assertEqual(response.context["path_suggestion"].category.slug, "fondamentaux")

    def test_empty_theme_is_404(self):
        GuideCategory.objects.create(name="Vide", slug="vide")
        self.assertEqual(
            self.client.get(reverse("catalogue:path_detail", args=["vide"])).status_code, 404
        )

    def test_navigation_menu(self):
        response = self.client.get(reverse("catalogue:path_list"))
        self.assertContains(
            response,
            'title="Guides, parcours, référentiels, certifications et lexique" aria-current="page"',
        )
        self.assertContains(response, "Ma progression")  # menu du compte
        self.assertContains(response, "Déconnexion")
