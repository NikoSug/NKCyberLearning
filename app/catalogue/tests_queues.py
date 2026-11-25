"""Files d'attente des générations : ordre manuel (pris en compte par les services) et retrait."""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CONTENT_MANAGERS_GROUP
from courses.models import Course
from podcasts.models import Series

from . import narration, queues, quizgen
from .models import Guide, GuideCategory, GuideDocument, Narration, NarrationSection, QuizDraft

User = get_user_model()


class QueueTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        now = timezone.now()
        self.guides = [
            Guide.objects.create(title=f"Guide {n}", slug=f"guide-{n}") for n in range(1, 5)
        ]
        self.drafts = [QuizDraft.objects.create(guide=g, requested_at=now + timedelta(minutes=n))
                       for n, g in enumerate(self.guides)]
        self.manager = User.objects.create_user("gestion", password="Un-mot-de-passe-solide-42!")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def order(self, kind="qcm"):
        return [row["title"] for row in queues.rows(kind)]

    def test_moves_change_what_is_generated_next(self):
        self.assertEqual(self.order(), ["Guide 1", "Guide 2", "Guide 3", "Guide 4"])
        self.assertEqual(queues.move("qcm", self.drafts[2].pk, "haut"), 1)
        queues.move("qcm", self.drafts[0].pk, "bas")
        queues.move("qcm", self.drafts[3].pk, "monter")
        self.assertEqual(self.order(), ["Guide 3", "Guide 4", "Guide 2", "Guide 1"])
        QuizDraft.objects.create(guide=Guide.objects.create(title="Guide 5", slug="guide-5"))
        self.assertEqual(self.order()[-1], "Guide 5")  # mis en file après le classement manuel
        self.assertEqual(
            quizgen.take_next().guide.title, "Guide 3"
        )  # le service suit l'ordre choisi
        self.assertIsNone(
            queues.move("qcm", self.drafts[2].pk, "bas")
        )  # en cours : plus dans la file

    def test_remove_and_clear(self):
        self.assertEqual(queues.remove("qcm", self.drafts[1].pk), "Guide 2")
        self.assertEqual(
            QuizDraft.objects.get(pk=self.drafts[1].pk).status, QuizDraft.Status.DISCARDED
        )
        self.assertIsNone(queues.remove("qcm", self.drafts[1].pk))
        self.assertEqual(queues.clear("qcm"), 3)
        self.assertEqual(self.order(), [])

    def test_audio_user_requests_stay_first_unless_moved_behind(self):
        Narration.objects.create(guide=self.guides[0], priority=narration.BULK_PRIORITY)
        bulk = Narration.objects.create(guide=self.guides[1], priority=narration.BULK_PRIORITY)
        Narration.objects.create(guide=self.guides[2], priority=narration.USER_PRIORITY)
        self.assertEqual(self.order("audio"), ["Guide 3", "Guide 1", "Guide 2"])
        queues.move(
            "audio", bulk.pk, "haut"
        )  # devant une demande d'utilisateur : devient prioritaire
        self.assertEqual(self.order("audio"), ["Guide 2", "Guide 3", "Guide 1"])
        self.assertEqual(narration.take_next().guide.title, "Guide 2")

    def test_removing_an_audio_regeneration_restores_the_old_version(self):
        old = Narration.objects.create(
            guide=self.guides[0], version=1, priority=narration.USER_PRIORITY
        )
        section = NarrationSection(narration=old, position=1, title="Chapitre", text="Texte")
        section.audio.save("ancien.mp3", ContentFile(b"ID3"), save=True)
        fresh = Narration.objects.create(guide=self.guides[1])
        queues.remove("audio", old.pk)
        queues.remove("audio", fresh.pk)
        old.refresh_from_db()
        self.assertEqual((old.status, old.sections_total), (Narration.Status.READY, 1))
        self.assertFalse(
            Narration.objects.filter(pk=fresh.pk).exists()
        )  # rien d'écoutable : supprimée

    def test_podcast_waiting_for_recording_goes_back_to_review(self):
        category = GuideCategory.objects.create(name="Réseau", slug="reseau")
        series = Series.objects.create(
            category=category,
            title="Série",
            slug="serie",
            status=Series.Status.TO_RECORD,
            approved_at=timezone.now(),
        )
        queues.remove("podcast", series.pk)
        series.refresh_from_db()
        self.assertEqual((series.status, series.approved_at), (Series.Status.REVIEW, None))

    def test_course_queue_keeps_themes_first_by_default(self):
        category = GuideCategory.objects.create(name="Réseau", slug="reseau")
        Course.objects.create(
            kind=Course.Kind.GUIDE, guide=self.guides[0], title="Cours guide", slug="c-guide"
        )
        Course.objects.create(
            kind=Course.Kind.THEME, category=category, title="Cours thème", slug="c-theme"
        )
        self.assertEqual(self.order("cours"), ["Cours thème", "Cours guide"])

    def test_pages_and_permissions(self):
        url = reverse("catalogue:generation_queue", args=["qcm"])
        self.client.force_login(
            User.objects.create_user("lea", password="Un-mot-de-passe-solide-42!")
        )
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.manager)
        page = self.client.get(url)
        self.assertContains(page, "Guide 4")
        self.assertContains(page, 'value="haut"', count=4)
        self.assertEqual(
            self.client.get(reverse("catalogue:generation_queue", args=["inconnu"])).status_code,
            404,
        )
        action = reverse("catalogue:generation_queue_action", args=["qcm"])
        response = self.client.post(action, {"action": "descendre", "item": self.drafts[0].pk})
        self.assertRedirects(response, f"{url}#e{self.drafts[0].pk}", fetch_redirect_response=False)
        self.assertEqual(self.order()[:2], ["Guide 2", "Guide 1"])
        self.client.post(action, {"action": "retirer", "item": self.drafts[3].pk})
        self.client.post(action, {"action": "vider"})
        self.assertEqual(self.order(), [])
        dashboard = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        for kind in ("cours", "podcast", "lexique", "audio"):
            self.assertEqual(
                self.client.get(reverse("catalogue:generation_queue", args=[kind])).status_code, 200
            )


class OutdatedAudioTests(TestCase):
    """Une version audio d'un système précédent doit pouvoir être régénérée par les boutons de mise
    en file."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.guides = []
        for slug in ("ancien", "absent", "a-jour"):
            guide = Guide.objects.create(title=slug.capitalize(), slug=slug)
            GuideDocument(guide=guide, label="PDF").file.save(
                f"{slug}.pdf", ContentFile(b"%PDF-1.4"), save=True
            )
            self.guides.append(guide)
        Narration.objects.create(guide=self.guides[0], status=Narration.Status.READY, version=1)
        Narration.objects.create(guide=self.guides[2], status=Narration.Status.READY,
                                 version=narration.narration_text.VERSION)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def test_outdated_versions_are_queued_after_missing_ones(self):
        from . import batches, generation
        self.assertEqual(batches.candidates("audio"), [self.guides[1], self.guides[0]])
        stats = generation.audio_stats()
        self.assertEqual((stats["covered"], stats["outdated"]), (1, 1))
        self.assertEqual(batches.queue("audio"), 2)
        old = Narration.objects.get(guide=self.guides[0])
        self.assertEqual(
            (old.status, old.version), (Narration.Status.PENDING, 1)
        )  # régénération demandée
        self.assertEqual(batches.candidates("audio"), [])
