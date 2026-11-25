"""Tableau de bord des générations : pause, reprise sans perte du travail fait, statistiques."""
import io
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CONTENT_MANAGERS_GROUP
from courses.models import Course

from . import generation, narration, narration_text, quizgen
from .models import GenerationControl, Glossary, Guide, GuideDocument, Narration, QuizDraft
from .tests_quizgen import SECTIONS as QUIZ_SECTIONS
from .tests_narration import fake_wav, offline_ai
from .tests_quizgen import fake_chat

User = get_user_model()
Kind = GenerationControl.Kind
AUDIO_SECTIONS = [
    narration_text.Section(f"Chapitre {n}", f"Texte du chapitre {n}.") for n in range(1, 4)
]
PCM = (b"\x00\x00" * 22050, 22050)


def pause_after(calls, kind):
    """Point d'étape qui demande la pause après `calls` passages."""
    seen = []

    def checkpoint():
        seen.append(1)
        if len(seen) > calls:
            generation.set_paused(kind, True)
        generation.checkpoint(kind)
    return checkpoint


class MediaTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.guide = Guide.objects.create(title="Guide pare-feu", slug="pare-feu")
        document = GuideDocument(guide=self.guide, label="PDF")
        document.file.save("pare-feu.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)


class PauseResumeTests(MediaTestCase):
    def test_audio_pause_keeps_chapters_and_resumes(self):
        rewrite = offline_ai(self)
        narration.request(self.guide)
        job = narration.take_next()
        with mock.patch.object(narration_text, "extract_sections", return_value=AUDIO_SECTIONS), \
                mock.patch.object(narration, "synthesize", return_value=PCM) as synthesize:
            with self.assertRaises(
                generation.Paused
            ):  # 3 chapitres réécrits, pause pendant la lecture du 2e
                narration.generate(job, checkpoint=pause_after(4, Kind.AUDIO))
            narration.suspend(job)
            self.assertEqual(synthesize.call_count, 1)
            self.assertEqual(Narration.objects.get(pk=job.pk).status, Narration.Status.PENDING)

            self.assertTrue(generation.is_paused(Kind.AUDIO))
            generation.set_paused(Kind.AUDIO, False)
            job = narration.take_next()
            with mock.patch.object(narration, "voice_name", return_value="siwis"):
                narration.generate(job, checkpoint=lambda: generation.checkpoint(Kind.AUDIO))
            self.assertEqual(
                synthesize.call_count, 3
            )  # chapitre 1 repris tel quel, 2 et 3 synthétisés
            self.assertEqual(
                rewrite.call_count, 3
            )  # réécritures gardées : pas de nouvel appel à l'IA
        job.refresh_from_db()
        self.assertEqual((job.status, job.sections.count()), (Narration.Status.READY, 3))
        self.assertEqual(job.duration_seconds, 3.0)

    def test_pause_acts_within_a_long_chapter(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = fake_wav(0.1)
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            with self.assertRaises(generation.Paused):
                narration.synthesize(
                    "Une phrase assez longue. " * 300, checkpoint=pause_after(2, Kind.AUDIO)
                )
        self.assertEqual(
            urlopen.call_count, 2
        )  # arrêt après le 2e morceau, sans attendre la fin du chapitre

    def test_service_start_recovers_interrupted_work_at_once(self):
        narration.request(self.guide)
        narration.take_next()
        quizgen.request(self.guide)
        quizgen.take_next()
        self.assertEqual(
            narration.recover_stale(), 0
        )  # récent : peut-être encore en cours ailleurs
        self.assertEqual(quizgen.recover_stale(), 0)
        self.assertEqual(narration.recover_stale(immediate=True), 1)
        self.assertEqual(quizgen.recover_stale(immediate=True), 1)

    def test_missing_pdf_gives_a_clear_error(self):
        GuideDocument.objects.filter(guide=self.guide).update(file="guides/absent.pdf")
        quizgen.request(self.guide)
        with self.assertRaisesMessage(quizgen.GenerationError, "volume des médias"):
            quizgen.generate(quizgen.take_next())
        narration.request(self.guide)
        with self.assertRaisesMessage(narration.NarrationError, "volume des médias"):
            narration.generate(narration.take_next())

    def test_too_many_failures_in_a_row_pause_the_generation(self):
        for _ in range(generation.FAILURE_LIMIT - 1):
            self.assertFalse(generation.record_failure(Kind.QUIZ, "modèle injoignable"))
        generation.record_success(Kind.QUIZ)  # un succès remet le compteur à zéro
        for _ in range(generation.FAILURE_LIMIT - 1):
            generation.record_failure(Kind.QUIZ, "modèle injoignable")
        self.assertFalse(generation.is_paused(Kind.QUIZ))
        self.assertTrue(generation.record_failure(Kind.QUIZ, "modèle injoignable"))
        item = generation.control(Kind.QUIZ)
        self.assertTrue(item.paused)
        self.assertIn("5 échecs de suite", item.note)
        generation.set_paused(Kind.QUIZ, False)
        self.assertEqual(
            (generation.control(Kind.QUIZ).failures, generation.control(Kind.QUIZ).note), (0, "")
        )

    def test_changed_chapter_is_synthesized_again(self):
        offline_ai(self)
        narration.request(self.guide)
        job = narration.take_next()
        with mock.patch.object(narration_text, "extract_sections", return_value=AUDIO_SECTIONS), \
                mock.patch.object(narration, "synthesize", return_value=PCM), \
                mock.patch.object(narration, "voice_name", return_value="siwis"):
            narration.generate(job)
        changed = [AUDIO_SECTIONS[0], narration_text.Section("Chapitre 2", "Texte corrigé.")]
        with mock.patch.object(narration_text, "extract_sections", return_value=changed), \
                mock.patch.object(narration, "synthesize", return_value=PCM) as synthesize, \
                mock.patch.object(narration, "voice_name", return_value="siwis"):
            narration.generate(job)
        self.assertEqual(synthesize.call_count, 1)
        self.assertEqual(
            list(job.sections.values_list("text", flat=True)),
            ["Texte du chapitre 1.", "Texte corrigé."],
        )

    def test_quiz_pause_keeps_series_and_resumes(self):
        quizgen.request(self.guide)
        draft = quizgen.take_next()
        with mock.patch.object(narration_text, "extract_sections", return_value=QUIZ_SECTIONS), \
                mock.patch.object(quizgen, "chat", side_effect=fake_chat):
            with self.assertRaises(generation.Paused):
                quizgen.generate(draft, checkpoint=pause_after(2, Kind.QUIZ))
            quizgen.suspend(draft)
            self.assertEqual(sorted(set(draft.questions.values_list("series", flat=True))), [1, 2])
            generation.set_paused(Kind.QUIZ, False)
            draft = quizgen.take_next()
            quizgen.generate(draft, checkpoint=lambda: generation.checkpoint(Kind.QUIZ))
        self.assertEqual((draft.status, draft.questions.count()), (QuizDraft.Status.REVIEW, 30))

    def test_paused_commands_take_nothing(self):
        narration.request(self.guide)
        quizgen.request(self.guide)
        generation.set_paused(Kind.AUDIO, True)
        generation.set_paused(Kind.QUIZ, True)
        out = io.StringIO()
        call_command("narrate", stdout=out)
        call_command("generate_quizzes", stdout=out)
        self.assertIn("Génération audio en pause", out.getvalue())
        self.assertIn("Génération des QCM en pause", out.getvalue())
        self.assertEqual(Narration.objects.get().status, Narration.Status.PENDING)
        self.assertEqual(QuizDraft.objects.get().status, QuizDraft.Status.PENDING)
        self.assertTrue(
            GenerationControl.objects.get(kind=Kind.AUDIO).last_seen
        )  # le service a signalé son activité


class CommandBreakerTests(TransactionTestCase):
    """La commande travaille dans des fils d'exécution : il faut des données réellement
    enregistrées."""

    def test_command_stops_the_queue_after_repeated_failures(self):
        for n in range(8):
            guide = Guide.objects.create(title=f"Guide {n}", slug=f"guide-{n}")
            GuideDocument.objects.create(guide=guide, label="PDF", file=f"guides/absent-{n}.pdf")
            quizgen.request(guide)
        call_command(
            "generate_quizzes", "--parallele", "1", stdout=io.StringIO(), stderr=io.StringIO()
        )
        self.assertEqual(
            QuizDraft.objects.filter(status=QuizDraft.Status.FAILED).count(),
            generation.FAILURE_LIMIT,
        )
        self.assertEqual(
            QuizDraft.objects.filter(status=QuizDraft.Status.PENDING).count(),
            8 - generation.FAILURE_LIMIT,
        )
        self.assertTrue(generation.is_paused(Kind.QUIZ))


class DashboardTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user("gestion", password="Un-mot-de-passe-solide-42!")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.client.force_login(self.manager)

    def action(self, kind, **data):
        return self.client.post(
            reverse("catalogue:generation_action", args=[kind]), data, follow=True
        )

    def test_reserved_to_content_managers(self):
        learner = User.objects.create_user("apprenant", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(learner)
        self.assertEqual(
            self.client.get(reverse("catalogue:generation_dashboard")).status_code, 403
        )
        self.assertEqual(self.action(Kind.QUIZ, action="pause").status_code, 403)
        self.assertFalse(generation.is_paused(Kind.QUIZ))

    def test_dashboard_pause_resume_and_queue(self):
        draft = QuizDraft.objects.create(
            guide=self.guide,
            status=QuizDraft.Status.REVIEW,
            started_at=timezone.now() - timedelta(minutes=3),
            generated_at=timezone.now(),
        )
        response = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(response, 'id="qcm"')
        self.assertContains(response, 'id="audio"')
        self.assertContains(
            response, 'class="status error">Service arrêté', count=10
        )  # tuile et carte de chacun des 5 services
        self.assertContains(
            response, '<dt>Moyenne</dt><dd class="small-value">3 min 00 s</dd>', html=False
        )
        quiz = next(item for item in response.context["services"] if item["kind"] == "qcm")
        self.assertEqual(quiz["s"]["chart"]["total"], 1)

        response = self.action(Kind.AUDIO, action="pause")
        self.assertTrue(generation.is_paused(Kind.AUDIO))
        self.assertContains(response, "En pause")
        self.assertEqual(GenerationControl.objects.get(kind=Kind.AUDIO).paused_by, self.manager)
        self.action(Kind.AUDIO, action="reprendre")
        self.assertFalse(generation.is_paused(Kind.AUDIO))

        self.action(Kind.AUDIO, action="file", nombre="tout")
        self.assertEqual(Narration.objects.get(guide=self.guide).priority, narration.BULK_PRIORITY)
        draft.status = QuizDraft.Status.FAILED
        draft.save()
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")), "Tout réessayer"
        )
        self.action(Kind.QUIZ, action="relancer", guide=self.guide.pk)
        self.assertTrue(
            QuizDraft.objects.filter(guide=self.guide, status=QuizDraft.Status.PENDING).exists()
        )
        other = Guide.objects.create(title="Autre", slug="autre")
        GuideDocument.objects.create(guide=other, label="PDF", file="guides/autre.pdf")
        QuizDraft.objects.create(
            guide=other, status=QuizDraft.Status.FAILED, error="PDF introuvable"
        )
        Narration.objects.filter(guide=self.guide).update(
            status=Narration.Status.FAILED, error="tts"
        )
        self.action(Kind.QUIZ, action="relancer-echecs")
        self.assertTrue(
            QuizDraft.objects.filter(guide=other, status=QuizDraft.Status.PENDING).exists()
        )
        self.action(Kind.AUDIO, action="relancer-echecs")
        self.assertEqual(Narration.objects.get(guide=self.guide).status, Narration.Status.PENDING)
        self.assertEqual(self.client.post(reverse("catalogue:generation_action", args=["autre"]),
                                          {"action": "pause"}).status_code, 404)

    def test_service_alive_after_heartbeat(self):
        generation.heartbeat(Kind.QUIZ, workers=2)
        stats = generation.quiz_stats()
        self.assertTrue(stats["alive"])
        self.assertEqual(stats["control"].workers, 2)
        GenerationControl.objects.filter(kind=Kind.QUIZ).update(
            last_seen=timezone.now() - timedelta(minutes=10)
        )
        self.assertFalse(generation.quiz_stats()["alive"])

    def test_gaming_mode_cuts_everything_then_restores(self):
        generation.set_paused(Kind.AUDIO, True)  # pause posée à part : doit survivre au mode jeu
        with mock.patch.object(generation, "unload_model") as unload:
            response = self.client.post(reverse("catalogue:generation_action", args=["jeu"]),
                                        {"action": "pause", "next": reverse("catalogue:dashboard")})
        self.assertRedirects(response, reverse("catalogue:dashboard"))
        unload.assert_called_once()
        self.assertTrue(generation.is_paused(Kind.QUIZ))
        self.assertNotContains(self.client.get(reverse("catalogue:dashboard")), "Une question ?")
        self.assertContains(self.client.get(reverse("catalogue:dashboard")), "Quitter le mode jeu")
        response = self.client.post(
            reverse("catalogue:assistant_ask"),
            '{"question": "C\'est quoi le MFA ?"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("mode jeu", response.json()["error"])
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")),
            'class="status warn">Mode jeu',
        )

        self.client.post(reverse("catalogue:generation_action", args=["jeu"]),
                         {"action": "reprendre", "next": "https://ailleurs.example/"})
        self.assertFalse(generation.is_paused(Kind.QUIZ))
        self.assertTrue(generation.is_paused(Kind.AUDIO))
        self.assertContains(self.client.get(reverse("catalogue:dashboard")), "Une question ?")
        out = io.StringIO()
        with mock.patch.object(generation, "unload_model"):
            call_command("mode_jeu", "on", stdout=out)
        self.assertIn("activé", out.getvalue())
        self.assertTrue(generation.gaming())

    def test_same_progress_format_for_every_generation(self):
        now = timezone.now()
        jobs = [
            QuizDraft.objects.create(
                guide=self.guide,
                status=QuizDraft.Status.GENERATING,
                progress="Série 3 sur 6",
                started_at=now,
            ),
            Glossary.objects.create(
                guide=self.guide,
                status=Glossary.Status.GENERATING,
                progress="Partie 2 sur 4",
                started_at=now,
            ),
            Narration.objects.create(
                guide=self.guide,
                status=Narration.Status.PROCESSING,
                sections_total=10,
                sections_done=4,
                started_at=now,
            ),
            Course.objects.create(
                kind=Course.Kind.GUIDE,
                guide=self.guide,
                title="Cours",
                slug="cours",
                status=Course.Status.GENERATING,
                progress="Leçon 1 sur 3",
                started_at=now,
                plan=[{"titre": "A"}, {"titre": "B"}, {"titre": "C"}],
            ),
        ]
        progress = [generation.job_progress(job) for job in jobs]
        self.assertEqual(
            [set(p) for p in progress], [{"label", "done", "total", "percent", "elapsed"}] * 4
        )
        self.assertEqual(
            [(p["done"], p["total"]) for p in progress], [(2, 6), (1, 4), (4, 10), (1, 5)]
        )
        self.assertEqual(progress[2]["label"], "Chapitre 5 sur 10")
        response = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(response, 'class="job-progress"', count=4)

    def test_same_timing_stats_for_every_generation(self):
        """Dernière durée, moyenne, temps écoulé de la tâche en cours et reste estimé, pour chaque
        type."""
        now = timezone.now()
        other = Guide.objects.create(title="Autre guide", slug="autre-guide")
        QuizDraft.objects.create(
            guide=self.guide,
            status=QuizDraft.Status.REVIEW,
            started_at=now - timedelta(minutes=30),
            generated_at=now - timedelta(minutes=26),
        )
        QuizDraft.objects.create(
            guide=other,
            status=QuizDraft.Status.PUBLISHED,
            started_at=now - timedelta(hours=2),
            generated_at=now - timedelta(hours=2, minutes=-8),
        )
        QuizDraft.objects.create(
            guide=other, status=QuizDraft.Status.GENERATING, started_at=now - timedelta(minutes=2)
        )
        QuizDraft.objects.create(guide=other, status=QuizDraft.Status.PENDING)
        Glossary.objects.create(
            guide=self.guide,
            status=Glossary.Status.REVIEW,
            started_at=now - timedelta(minutes=5),
            generated_at=now - timedelta(minutes=3),
        )
        Narration.objects.create(
            guide=self.guide,
            status=Narration.Status.READY,
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=10),
        )
        Course.objects.create(kind=Course.Kind.GUIDE, guide=self.guide, title="Cours", slug="cours",
                              status=Course.Status.REVIEW, started_at=now - timedelta(hours=1),
                              generated_at=now - timedelta(minutes=45))

        with mock.patch.object(
            timezone, "now", return_value=now
        ):  # heure figée : pas de seconde de décalage
            quiz = generation.quiz_stats()["pace"]
        self.assertEqual(set(quiz), {"last", "average", "samples", "elapsed", "remaining"})
        self.assertEqual(
            (quiz["last"], quiz["average"], quiz["samples"]), ("4 min 00 s", "6 min 00 s", 2)
        )
        self.assertEqual(quiz["elapsed"], "2 min 00 s")
        self.assertEqual(
            quiz["remaining"], "10 min 00 s"
        )  # 6 min pour celui en file + 4 min pour finir l'actuel
        self.assertEqual(generation.glossary_stats()["pace"]["last"], "2 min 00 s")
        self.assertEqual(generation.audio_stats()["pace"]["average"], "10 min 00 s")
        from courses.stats import course_stats
        self.assertEqual(course_stats()["pace"]["last"], "15 min 00 s")

        response = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(response, 'aria-label="Temps de génération"', count=5)
        self.assertContains(response, "<dt>Tâche en cours</dt>", count=5)
        self.assertContains(response, "<dt>Faits</dt>", count=5)

    def test_timing_without_timestamps_shows_a_dash(self):
        QuizDraft.objects.create(
            guide=self.guide, status=QuizDraft.Status.REVIEW
        )  # ancienne génération
        QuizDraft.objects.create(guide=self.guide, status=QuizDraft.Status.GENERATING)
        pace = generation.quiz_stats()["pace"]
        self.assertEqual(
            (pace["last"], pace["average"], pace["elapsed"], pace["remaining"]), ("", "", "", "")
        )
        response = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(response, '<dt>Dernière</dt><dd class="small-value">—</dd>', count=5)
        self.assertContains(
            response, '<dt>Reste estimé</dt><dd class="small-value">—</dd>', count=5
        )

    def test_task_past_the_average_is_almost_done(self):
        now = timezone.now()
        running = QuizDraft(started_at=now - timedelta(minutes=9))
        with mock.patch.object(timezone, "now", return_value=now):
            pace = generation._pace([timedelta(minutes=5)], 0, [running])
        self.assertEqual((pace["elapsed"], pace["remaining"]), ("9 min 00 s", "moins d'une minute"))

    def test_human_duration(self):
        self.assertEqual(generation.human_duration(timedelta(seconds=170)), "2 min 50 s")
        self.assertEqual(generation.human_duration(timedelta(hours=4, minutes=10)), "4 h 10")
        self.assertEqual(generation.human_duration(timedelta(days=1, hours=3)), "1 j 3 h")
        self.assertEqual(generation.human_duration(None), "")
