"""Installation sans IA (AI_ENABLED=0) : l'application fonctionne, les fonctions d'IA sont masquées
et refusées."""

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP, User
from courses.models import Course

from .models import Guide, Narration


@override_settings(AI_ENABLED=False)
class NoAITests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("gestion", is_staff=True)
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.client.force_login(self.manager)
        self.guide = Guide.objects.create(title="Guide", slug="guide", anssi_id="guide")

    def test_pages_work_without_ai_entry_points(self):
        page = self.client.get(reverse("catalogue:dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-assistant")
        self.assertNotContains(page, "📊 Générations")
        self.assertNotContains(page, "Mode jeu")
        hub = self.client.get(reverse("studio:home"))
        self.assertNotContains(hub, reverse("studio:list", args=["podcast"]))
        self.assertEqual(
            self.client.get(reverse("studio:create", args=["podcast"])).status_code, 404
        )
        listen = self.client.get(reverse("catalogue:guide_listen", args=["guide"]))
        self.assertContains(listen, "La synthèse vocale n'est pas activée")

    def test_ai_requests_are_refused(self):
        ask = self.client.post(
            reverse("catalogue:assistant_ask"), "{}", content_type="application/json"
        )
        self.assertEqual(ask.status_code, 503)
        self.client.post(reverse("catalogue:guide_listen", args=["guide"]))
        self.assertFalse(Narration.objects.exists())
        self.client.post(reverse("catalogue:generation_action", args=["jeu"]), {"action": "pause"})
        self.client.post(
            reverse("studio:create", args=["cours"]),
            {"mode": "ia", "title": "Cours", "level": "debutant"},
        )
        self.assertFalse(Course.objects.exists())

    @override_settings(AI_ENABLED=True)
    def test_ai_on_shows_entry_points(self):
        self.assertContains(self.client.get(reverse("catalogue:dashboard")), "📊 Générations")
