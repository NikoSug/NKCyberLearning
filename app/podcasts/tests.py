"""Podcasts : écriture (modèle simulé), relecture, enregistrement (voix simulée), écoute et XP."""

import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP
from catalogue import generation, narration, narration_text
from catalogue.models import Guide, GuideCategory, GuideDocument
from catalogue.tests_narration import offline_ai
from courses import coursegen
from progression.models import XPEvent

from . import podcastgen
from .models import HOSTS, Episode, PodcastProgress, Series

User = get_user_model()
Status = Series.Status
SECTIONS = [
    narration_text.Section(
        f"Guide d'hygiène » Chapitre {n}", f"Texte du chapitre {n} sur la sécurité. " * 60
    )
    for n in range(1, 4)
]
PCM = (b"\x01\x00" * 24000 * 3, 24000)  # 3 secondes


def dialogue(turns=12, words=50):
    return [
        {"voix": HOSTS[n % 2], "texte": f"{HOSTS[n % 2]} : réplique {n}. " + "mot " * words}
        for n in range(turns)
    ]


def fake_chat(messages, schema, temperature=0.5):
    if "episodes" in schema["properties"]:
        return {
            "titre": "Cyber en clair",
            "presentation": "Une émission pour comprendre.",
            "episodes": [
                {"titre": f"{n}. Épisode {n}", "angle": f"Question {n}", "mots_cles": ["sécurité"]}
                for n in range(1, 4)
            ],
        }
    return {"resume": "On parle sécurité.", "repliques": dialogue()}


class MediaTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.category = GuideCategory.objects.create(name="Fondamentaux", slug="fondamentaux")
        self.guide = Guide.objects.create(
            title="Guide d'hygiène",
            slug="hygiene",
            category=self.category,
            summary="Les règles de base.",
        )
        document = GuideDocument(guide=self.guide, label="PDF")
        document.file.save("hygiene.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)
        offline_ai(self)  # ni IA ni carte graphique à décharger

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def write(self, chat=fake_chat):
        podcastgen.request_theme(self.category)
        with mock.patch.object(coursegen, "guide_sections", return_value=SECTIONS), \
                mock.patch.object(podcastgen.quizgen, "chat", side_effect=chat) as fake:
            series = podcastgen.generate(podcastgen.take_next())
        series.chat_calls = fake.call_count
        return series

    def record(self, series):
        podcastgen.approve(series, None)
        with mock.patch.object(narration, "synthesize_dialogue", return_value=PCM) as synthesize, \
                mock.patch.object(narration, "voice_name", return_value="Kyutai TTS"):
            series = podcastgen.record(podcastgen.take_next())
        series.synthesize_calls = synthesize.call_count
        return series


class ScriptTests(TestCase):
    def test_turns_are_cleaned_and_merged(self):
        script = podcastgen.normalize_script([
            {"speaker": 0, "text": "Clara : **Bonjour** [musique] à tous !"},
            {"speaker": 0, "text": "(rires) On commence."},
            {"speaker": 1, "text": "  "},
            {"speaker": 1, "text": "Hugo: Allons-y."}])
        self.assertEqual(script, [{"speaker": 0, "text": "Bonjour à tous ! On commence."},
                                  {"speaker": 1, "text": "Allons-y."}])

    def test_filler_and_episode_numbers_are_removed(self):
        self.assertEqual(
            podcastgen.clean_turn("C'est une excellente question, Clara. Imaginez une porte."),
            "Imaginez une porte.",
        )
        self.assertEqual(
            podcastgen.clean_turn("Absolument ! Tout à fait. il faut sauvegarder."),
            "Il faut sauvegarder.",
        )
        self.assertEqual(
            podcastgen.clean_turn("Exactement comme une clé."), "Exactement comme une clé."
        )
        self.assertEqual(podcastgen.clean_title("Épisode 2 : Les sauvegardes"), "Les sauvegardes")

    def test_review_text_round_trip(self):
        script = [{"speaker": 0, "text": "Bonjour."}, {"speaker": 1, "text": "Salut."}]
        self.assertEqual(podcastgen.parse_script(podcastgen.script_text(script)), script)
        self.assertEqual(podcastgen.parse_script("hugo : Une idée.\nqui continue ici"),
                         [{"speaker": 1, "text": "Une idée. qui continue ici"}])
        with self.assertRaises(podcastgen.GenerationError):
            podcastgen.parse_script("Sans nom au début")

    def test_problems_are_listed(self):
        problems = podcastgen.script_problems([{"speaker": 0, "text": "Bonjour."}])
        self.assertEqual(len(problems), 3)  # trop peu de répliques, une seule voix, trop court

    def test_dialogue_is_sent_in_small_groups(self):
        turns = [
            {"speaker": n % 2, "text": "Une phrase assez longue pour remplir. " * 4}
            for n in range(10)
        ]
        groups = narration.dialogue_groups(turns)
        self.assertGreater(len(groups), 2)
        self.assertTrue(all(len(g) <= narration.DIALOGUE_TURNS for g in groups))
        self.assertTrue(
            all(sum(len(t["text"]) for t in g) <= narration.DIALOGUE_LIMIT for g in groups)
        )
        self.assertEqual([t["speaker"] for g in groups for t in g], [n % 2 for n in range(10)])


class GenerationTests(MediaTestCase):
    def test_series_is_written_then_reviewed(self):
        series = self.write()
        self.assertEqual(
            (series.status, series.title, series.episodes.count()),
            (Status.REVIEW, "Cyber en clair", 3),
        )
        episode = series.episodes.first()
        self.assertEqual(episode.title, "Épisode 1")  # numéro retiré
        self.assertEqual(episode.sources, "Guide d'hygiène")
        self.assertFalse(episode.script[0]["text"].startswith("Clara"))
        self.assertEqual(podcastgen.review_problems(series), [])

    def test_interrupted_writing_keeps_episodes(self):
        series = self.write()
        Episode.objects.filter(series=series, position=3).delete()
        series.status = Status.PENDING
        series.save()
        series = self.write()
        self.assertEqual(
            series.chat_calls, 2
        )  # plan et épisodes 1-2 gardés : seul l'épisode 3 est écrit (et vérifié)

    def test_unfaithful_lines_are_flagged_for_review(self):
        def chat(messages, schema, temperature=0.5):
            if "problemes" in schema["properties"]:
                return {
                    "problemes": [
                        {"replique": 3, "raison": "Absent des guides."},
                        {"replique": 99, "raison": "?"},
                        {"replique": 3, "raison": "Doublon."},
                    ]
                }
            return fake_chat(messages, schema)
        series = self.write(chat)
        episode = series.episodes.first()
        self.assertEqual(episode.flags, [{"index": 3, "reason": "Absent des guides."}])
        self.assertEqual(episode.lines[3]["flag"], "Absent des guides.")

    def test_bad_script_fails_after_retries(self):
        def chat(messages, schema, temperature=0.5):
            if "episodes" in schema["properties"]:
                return fake_chat(messages, schema)
            return {"resume": "Court.", "repliques": dialogue(turns=4)}
        with self.assertRaisesMessage(podcastgen.GenerationError, "moins de 10 répliques"):
            self.write(chat)

    def test_recording_publishes_the_series(self):
        series = self.record(self.write())
        self.assertEqual(
            (series.status, series.synthesize_calls, series.voice),
            (Status.PUBLISHED, 3, "Kyutai TTS"),
        )
        self.assertEqual(series.duration_seconds, 9.0)
        generation.unload_model.assert_called()  # IA déchargée avant la lecture
        narration.release_voice.assert_called()  # voix libérée à la fin
        with series.episodes.first().audio.open("rb") as handle:
            head = handle.read(3)
        self.assertTrue(
            head == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        )  # MP3

    def test_recordings_go_before_new_writing_and_resume(self):
        written = self.write()
        other = GuideCategory.objects.create(name="Réseaux", slug="reseaux")
        Guide.objects.create(title="Pare-feu", slug="pare-feu", category=other)
        podcastgen.request_theme(other)
        podcastgen.approve(written, None)
        self.assertEqual(podcastgen.take_next().pk, written.pk)  # validée : enregistrée en premier
        written.refresh_from_db()
        self.assertEqual(written.status, Status.RECORDING)
        podcastgen.suspend(written)
        self.assertEqual(written.status, Status.TO_RECORD)  # la pause garde la validation

    def test_queue_and_stats(self):
        self.assertEqual(list(podcastgen.themes_without_series()), [self.category])
        podcastgen.request_all()
        self.assertEqual(list(podcastgen.themes_without_series()), [])
        from .stats import podcast_stats
        stats = podcast_stats()
        self.assertEqual((stats["eligible"], stats["pending"], stats["missing"]), (1, 1, 0))
        self.assertEqual(
            (stats["pace"]["last"], stats["pace"]["remaining"]), ("", "")
        )  # rien de mesuré encore

    def test_writing_and_recording_durations_are_kept(self):
        """« started_at » repart à zéro à l'enregistrement : la durée de chaque étape est gardée à
        part."""
        series = self.write()
        self.assertIsNotNone(series.script_seconds)
        self.assertIsNone(series.recording_seconds)
        series = self.record(series)
        self.assertIsNotNone(series.script_seconds)
        self.assertIsNotNone(series.recording_seconds)

    def test_stats_add_writing_and_recording(self):
        from .stats import podcast_stats
        series = self.write()
        Series.objects.filter(pk=series.pk).update(
            status=Status.PUBLISHED,
            published_at=series.generated_at,
            script_seconds=600,
            recording_seconds=1200,
        )
        other = GuideCategory.objects.create(name="Réseaux", slug="reseaux")
        Guide.objects.create(title="Pare-feu", slug="pare-feu", category=other)
        waiting = podcastgen.request_theme(other)
        pace = podcast_stats()["pace"]
        self.assertEqual((pace["last"], pace["average"]), ("30 min 00 s", "30 min 00 s"))
        self.assertEqual(pace["remaining"], "30 min 00 s")
        self.assertEqual(pace["detail"], "écriture ≈ 10 min 00 s · enregistrement ≈ 20 min 00 s")
        Series.objects.filter(pk=waiting.pk).update(
            status=Status.TO_RECORD
        )  # déjà écrite : reste l'enregistrement
        self.assertEqual(podcast_stats()["pace"]["remaining"], "20 min 00 s")
        Series.objects.filter(pk=series.pk).update(
            script_seconds=None
        )  # ancienne série : écriture inconnue
        self.assertEqual(podcast_stats()["pace"]["last"], "20 min 00 s")


class PageTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("lea", password="mot-de-passe-solide-1")
        self.manager = User.objects.create_user("gestion", password="mot-de-passe-solide-1")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])

    def test_only_published_series_are_listed(self):
        series = self.write()
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("podcasts:podcast_list")), series.title)
        self.assertEqual(
            self.client.get(reverse("podcasts:series_detail", args=[series.slug])).status_code, 404
        )
        self.record(series)
        response = self.client.get(reverse("podcasts:podcast_list"))
        self.assertContains(response, "Cyber en clair")
        page = self.client.get(reverse("podcasts:series_detail", args=[series.slug]))
        self.assertContains(page, "data-player")
        self.assertContains(page, "host-1")  # transcription en dialogue
        audio = self.client.get(
            reverse("podcasts:episode_audio", args=[series.slug, 1]), HTTP_RANGE="bytes=0-9"
        )
        self.assertEqual(audio.status_code, 206)

    def test_listening_to_every_episode_gives_xp(self):
        series = self.record(self.write())
        self.client.force_login(self.user)
        url = reverse("podcasts:listen_progress", args=[series.slug])
        self.assertEqual(self.client.post(url, {"section": 9, "seconds": 0}).status_code, 400)
        result = self.client.post(url, {"section": 1, "seconds": 12.5}).json()
        self.assertEqual((result["done"], result["total"]), (0, 3))
        for position in (1, 2, 3):
            result = self.client.post(url, {"section": position, "seconds": 0, "ended": "1"}).json()
        self.assertTrue(result["completed"])
        self.assertIn("Série écoutée en entier", result["message"])
        self.assertEqual(
            XPEvent.objects.filter(user=self.user, ref__startswith="podcast").count(), 4
        )
        self.client.post(
            url, {"section": 1, "seconds": 0, "ended": "1"}
        )  # réécoute : pas d'XP en plus
        self.assertEqual(
            XPEvent.objects.filter(user=self.user, ref__startswith="podcast").count(), 4
        )
        self.assertIsNotNone(PodcastProgress.objects.get(user=self.user).completed_at)

    def test_review_edit_then_validate(self):
        series = self.write()
        Episode.objects.filter(series=series, position=2).update(
            flags=[{"index": 1, "reason": "Chiffre inventé."}]
        )
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("podcasts:review_series", args=[series.pk])).status_code, 403
        )
        self.client.force_login(self.manager)
        page = self.client.get(reverse("podcasts:review_series", args=[series.pk]), {"episode": 2})
        self.assertContains(page, "Épisode 2 sur 3")
        self.assertContains(page, "Chiffre inventé.")
        self.assertEqual(self.client.get(reverse("catalogue:review_list"), {"type": "podcast"})
                         .context["to_review"][0]["flag"], 1)
        episode = series.episodes.get(position=2)
        script = podcastgen.script_text(episode.script).replace("Réplique 1.", "Réplique corrigée.")
        self.client.post(reverse("podcasts:review_episode", args=[episode.pk]),
                         {"title": "Nouveau titre", "summary": "Résumé", "body": script})
        episode.refresh_from_db()
        self.assertEqual(
            (episode.title, episode.flags), ("Nouveau titre", [])
        )  # relu : remarques effacées
        self.assertIn("Réplique corrigée.", episode.script[1]["text"])
        self.client.post(reverse("podcasts:review_episode", args=[episode.pk]),
                         {"title": "Titre", "body": "Clara : Trop court."})
        episode.refresh_from_db()
        self.assertEqual(episode.title, "Nouveau titre")  # script refusé : rien n'est modifié
        self.client.post(
            reverse("podcasts:review_series_action", args=[series.pk]), {"action": "valider"}
        )
        series.refresh_from_db()
        self.assertEqual(series.status, Status.TO_RECORD)

    def test_rewrite_one_episode(self):
        series = self.write()
        self.client.force_login(self.manager)
        episode = series.episodes.get(position=2)
        self.client.post(
            reverse("podcasts:review_episode", args=[episode.pk]), {"action": "reecrire"}
        )
        series.refresh_from_db()
        self.assertEqual((series.status, series.episodes.count()), (Status.PENDING, 2))

    def test_review_hub_and_dashboard_show_podcasts(self):
        self.write()
        self.client.force_login(self.manager)
        hub = self.client.get(reverse("catalogue:review_list"), {"type": "podcast"})
        self.assertEqual(len(hub.context["to_review"]), 1)
        self.assertEqual(
            self.client.get(reverse("catalogue:review_list")).context["counts"]["podcast"], 1
        )
        dashboard = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(dashboard, 'id="podcast"')
        self.assertContains(dashboard, "podcastgen")
