"""Versions audio : nettoyage du texte, réécriture pour l'oral, file d'attente, génération (IA et
voix simulées), lecteur."""

import array
import io
import json
import shutil
import urllib.error
import tempfile
import wave
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from progression import xp

from . import generation, narration, narration_script, narration_text, quizgen
from .models import (
    Guide,
    GuideDocument,
    ListeningProgress,
    Narration,
    NarrationSection,
    ReadingListItem,
)

User = get_user_model()
Status = Narration.Status


def fake_wav(seconds=1.0, rate=22050):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x01\x00" * int(seconds * rate))
    return buffer.getvalue()


def offline_ai(test):
    """Ni IA ni carte graphique : la réécriture rend le texte tel quel, rien à décharger. Renvoie le
    faux `rewrite`."""
    rewrite = mock.Mock(side_effect=lambda section, known=(), checkpoint=None: section.text)
    for target, name, value in (
        (narration_script, "rewrite", rewrite),
        (generation, "unload_model", mock.Mock()),
        (narration, "release_voice", mock.Mock()),
    ):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        test.addCleanup(patcher.stop)
    return rewrite


class TextTests(TestCase):
    def test_clean_block_removes_what_is_not_read(self):
        repeated = {narration_text.signature("Recommandations SSH – 12")}
        self.assertIsNone(
            narration_text.clean_block("Recommandations SSH – 7", repeated)
        )  # en-tête répété
        self.assertIsNone(narration_text.clean_block("Page 3 sur 21"))
        self.assertIsNone(narration_text.clean_block("1 Préambule . . . . . . . . . . 3"))
        self.assertIsNone(
            narration_text.clean_block("ssh-keygen -t rsa -b 2048 -f /etc/ssh/key_rsa")
        )
        self.assertIsNone(narration_text.clean_block("Figure 1 – Schéma d'une redirection"))
        self.assertEqual(
            narration_text.clean_block(
                "n la clé privée doit être protégée [RefPx5] (voir https://cyber.gouv.fr)"
            ),
            "la clé privée doit être protégée",
        )
        self.assertEqual(
            narration_text.clean_block("Le serveur doit véri-\nfier la clé."),
            "Le serveur doit vérifier la clé.",
        )
        self.assertEqual(narration_text.clean_block("2.1 Le protocole SSH"), "Le protocole SSH")

    def test_paragraphs_are_rejoined_across_pages_and_headings_get_a_pause(self):
        text = narration_text.paragraphs_to_text(
            [
                "Transfert de fichiers",
                "Le cas d'usage concerne le",
                "sens montant et descendant.",
                "Conclusion",
            ]
        )
        self.assertEqual(
            text,
            "Transfert de fichiers.\nLe cas d'usage concerne le sens montant et"
            " descendant.\nConclusion.",
        )

    def test_speakable(self):
        self.assertEqual(narration_text.speakable("R1 1 : le RSSI valide le SI ; R12- aussi."),
                         "recommandation 11: le R S S I valide le S I; recommandation 12 aussi.")
        self.assertEqual(
            narration_text.speakable("Désactiver SSLv3"), "Désactiver SSLv3"
        )  # pas de faux sigle

    def test_outline_entries_without_page_are_skipped(self):
        reader = mock.Mock(
            outline=[
                mock.Mock(title="Introduction"),
                mock.Mock(title="Lien externe"),
                mock.Mock(title="Annexe"),
            ]
        )
        reader.get_destination_page_number.side_effect = [0, None, 4]
        self.assertEqual([(e.title, e.page) for e in narration_text.outline_tree(reader)],
                         [("Introduction", 0), ("Annexe", 4)])

    def test_chunks_cut_between_sentences(self):
        text = "Une phrase courte. " * 100 + "x" * 2600
        pieces = narration_text.chunks(text, limit=300)
        self.assertTrue(all(len(p) <= 300 for p in pieces))
        self.assertEqual("".join(p.replace(" ", "") for p in pieces), text.replace(" ", ""))

    def test_long_chapters_are_split_into_sub_chapters(self):
        Entry = narration_text.Entry
        tree = [
            Entry("Intro", 0, []),
            Entry(
                "Bonnes pratiques", 1, [Entry("Cryptographie", 2, []), Entry("Durcissement", 4, [])]
            ),
            Entry("Conclusion", 6, []),
        ]
        sizes = [1000, 500, 9000, 3000, 6000, 1000, 800]
        ranges = narration_text._flatten(tree, 7, sizes)
        self.assertEqual(
            ranges,
            [
                ("Intro", 0, 1),
                ("Bonnes pratiques", 1, 2),
                ("Bonnes pratiques — Cryptographie", 2, 4),
                ("Bonnes pratiques — Durcissement", 4, 6),
                ("Conclusion", 6, 7),
            ],
        )


class QueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.guide = Guide.objects.create(title="Guide SSH", slug="ssh")
        GuideDocument.objects.create(guide=self.guide, label="PDF", file="guides/ssh.pdf")

    def test_request_is_idempotent_and_user_requests_go_first(self):
        bulk = narration.request(self.guide, priority=narration.BULK_PRIORITY)
        self.assertEqual((bulk.status, bulk.priority), (Status.PENDING, 1))
        mine = narration.request(self.guide, self.user)
        self.assertEqual((Narration.objects.count(), mine.priority), (1, 0))

        other = Guide.objects.create(title="Autre", slug="autre")
        GuideDocument.objects.create(guide=other, label="PDF", file="guides/autre.pdf")
        narration.request(other, priority=narration.BULK_PRIORITY)
        self.assertEqual(narration.take_next().guide, self.guide)
        self.assertEqual(Narration.objects.get(guide=self.guide).status, Status.PROCESSING)

    def test_failed_or_outdated_versions_are_requeued(self):
        audio = narration.request(self.guide)
        narration.fail(audio, "boum")
        self.assertEqual(narration.request(self.guide).status, Status.PENDING)
        Narration.objects.filter(pk=audio.pk).update(
            status=Status.READY, version=narration_text.VERSION - 1
        )
        self.assertEqual(narration.request(self.guide).status, Status.PENDING)
        Narration.objects.filter(pk=audio.pk).update(
            status=Status.READY, version=narration_text.VERSION
        )
        self.assertEqual(narration.request(self.guide).status, Status.READY)

    def test_guides_without_pdf_cannot_be_narrated(self):
        with self.assertRaises(narration.NarrationError):
            narration.request(Guide.objects.create(title="Sans PDF", slug="sans-pdf"))

    def test_stale_generations_are_recovered(self):
        audio = narration.request(self.guide)
        Narration.objects.filter(pk=audio.pk).update(status=Status.PROCESSING,
                                                     started_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(narration.recover_stale(), 1)
        self.assertEqual(narration.queue_position(Narration.objects.get(pk=audio.pk)), 1)


class ScriptTests(TestCase):
    """Réécriture d'un chapitre pour l'oral par l'IA locale."""

    def test_rewrite_by_passages_and_known_acronyms_are_not_expanded_again(self):
        section = narration_text.Section(
            "Cloisonnement", "L'ANSSI recommande le cloisonnement. " * 120
        )
        replies = iter(
            [
                "L'ANSSI, l'Agence nationale de la sécurité des systèmes d'information,"
                " recommande… "
                * 20,
                "Le cloisonnement des SI réduit les risques. " * 40,
            ]
        )
        with mock.patch.object(
            quizgen,
            "chat",
            side_effect=lambda messages, schema, temperature, max_tokens: {"texte": next(replies)},
        ) as chat:
            text = narration_script.rewrite(section, known={"RGPD"})
        self.assertEqual(chat.call_count, 2)  # deux passages
        first, second = (call.args[0][1]["content"] for call in chat.call_args_list)
        self.assertIn("(début du chapitre)", first)
        self.assertIn("Sigles déjà développés plus haut : RGPD.", first)
        self.assertIn("ANSSI, RGPD", second)  # sigle présenté dans le premier passage
        self.assertTrue(
            text.startswith("L'ANSSI, l'Agence nationale") and "\n\nLe cloisonnement des SI" in text
        )

    def test_too_short_answer_falls_back_to_the_automatic_adaptation(self):
        section = narration_text.Section(
            "Titre", "Le SI doit être cloisonné en zones de confiance distinctes."
        )
        with mock.patch.object(quizgen, "chat", return_value={"texte": "Cloisonnez."}):
            self.assertEqual(
                narration_script.rewrite(section), narration_text.speakable(section.text)
            )

    def test_looping_model_is_retried_then_falls_back_without_failing_the_guide(self):
        section = narration_text.Section(
            "Titre", "Le SI doit être cloisonné en zones de confiance distinctes. " * 3
        )
        spoken = (
            "Le système d'information doit être cloisonné en zones de confiance distinctes. " * 3
        )
        # Premier essai coupé (le modèle bouclait), second réussi : la réécriture est gardée.
        with mock.patch.object(
            quizgen, "chat", side_effect=[quizgen.UnreadableAnswer("coupée"), {"texte": spoken}]
        ) as chat:
            self.assertEqual(narration_script.rewrite(section), spoken.strip())
        temperatures = [call.kwargs["temperature"] for call in chat.call_args_list]
        self.assertEqual(
            temperatures, [narration_script.TEMPERATURE, narration_script.RETRY_TEMPERATURE]
        )
        self.assertEqual(
            chat.call_args.kwargs["max_tokens"],
            narration_script.answer_budget(section.text.strip()),
        )
        # Deux essais trop longs : adaptation automatique, sans faire échouer la version audio.
        with mock.patch.object(quizgen, "chat", side_effect=quizgen.SlowAnswer("délai dépassé")):
            self.assertEqual(
                narration_script.rewrite(section), narration_text.speakable(section.text.strip())
            )

    def test_chat_caps_the_answer_and_reports_timeouts_as_slow_answers(self):
        sent = {}

        def fake_urlopen(request, timeout):
            sent.update(json.loads(request.data))
            raise TimeoutError("timed out")
        with mock.patch.object(quizgen.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(quizgen.SlowAnswer):
                quizgen.chat([{"role": "user", "content": "x"}], {}, max_tokens=1500)
        self.assertEqual(sent["options"]["num_predict"], 1500)

    def test_unreachable_ai_is_a_narration_error(self):
        section = narration_text.Section("Titre", "Texte.")
        with mock.patch.object(quizgen, "chat", side_effect=quizgen.GenerationError("injoignable")):
            with self.assertRaisesMessage(
                narration.NarrationError, "réécriture pour l'oral impossible"
            ):
                narration_script.rewrite(section)


class GenerationTests(TestCase):
    def setUp(self):
        self.rewrite = offline_ai(self)
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.guide = Guide.objects.create(title="Guide SSH", slug="ssh")
        document = GuideDocument(guide=self.guide, label="PDF")
        document.file.save("ssh.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def test_synthesize_concatenates_pieces(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = fake_wav(0.5)
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            pcm, rate = narration.synthesize("Première phrase. " * 150)
        self.assertGreater(urlopen.call_count, 1)  # plusieurs morceaux
        self.assertEqual(rate, 22050)
        self.assertGreater(len(pcm), urlopen.call_count * 0.5 * rate * 2)

    def test_generate_writes_one_mp3_per_section(self):
        sections = [narration_text.Section("Préambule", "Texte du préambule."),
                    narration_text.Section("Cryptographie", "Texte sur la cryptographie.")]
        audio = narration.request(self.guide)
        with mock.patch.object(
            narration_text, "extract_sections", return_value=sections
        ), mock.patch.object(
            narration, "synthesize", return_value=(b"\x00\x00" * 22050 * 2, 22050)
        ), mock.patch.object(
            narration, "voice_name", return_value="fr_FR-siwis-medium"
        ):
            narration.generate(narration.take_next())
        audio.refresh_from_db()
        self.assertEqual(
            (audio.status, audio.sections_done, audio.sections_total, audio.duration_seconds),
            (Status.READY, 4, 4, 4.0),
        )  # deux phases : réécriture puis lecture
        # L'IA libère la carte graphique avant la lecture
        generation.unload_model.assert_called_once()
        narration.release_voice.assert_called_once()
        first = audio.sections.get(position=1)
        self.assertEqual(first.title, "Préambule")
        with first.audio.open("rb") as handle:
            head = handle.read(3)
        self.assertTrue(
            head == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        )  # MP3

    def test_unreadable_pdf_fails_cleanly(self):
        narration.request(self.guide)
        job = narration.take_next()
        with mock.patch.object(narration_text, "extract_sections", return_value=[]):
            with self.assertRaises(narration.NarrationError):
                narration.generate(job)

    def test_voice_is_released_even_when_synthesis_fails(self):
        narration.request(self.guide)
        with mock.patch.object(
            narration_text, "extract_sections", return_value=[narration_text.Section("A", "B.")]
        ), mock.patch.object(
            narration, "synthesize", side_effect=narration.NarrationError("injoignable")
        ):
            with self.assertRaises(narration.NarrationError):
                narration.generate(narration.take_next())
        narration.release_voice.assert_called_once()
        self.assertEqual(
            NarrationSection.objects.get().text, "B."
        )  # la réécriture est gardée pour la reprise

    def test_full_graphics_memory_frees_the_ai_and_retries(self):
        busy = urllib.error.HTTPError(
            "http://tts:8000/synthesize", 503, "mémoire", {}, io.BytesIO(b"{}")
        )
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = fake_wav(0.2)
        with mock.patch("urllib.request.urlopen", side_effect=[busy, response]), \
                mock.patch.object(narration.time, "sleep"):
            _pcm, rate = narration.synthesize("Une phrase.")
        generation.unload_model.assert_called_once()
        self.assertEqual(rate, 22050)

    def test_voice_service_restarting_is_waited_for(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = fake_wav(0.2)
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with mock.patch(
            "urllib.request.urlopen", side_effect=[refused, TimeoutError("timed out"), response]
        ), mock.patch.object(narration.time, "sleep") as sleep:
            narration.synthesize("Une phrase.")
        self.assertEqual(
            [c.args[0] for c in sleep.call_args_list], list(narration.RETRY_DELAYS[:2])
        )
        self.assertTrue(generation.voice_active())  # synthèse en cours : l'assistant est en pause

    def test_voice_service_down_for_good_fails_after_a_few_minutes(self):
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with mock.patch(
            "urllib.request.urlopen", side_effect=refused
        ) as urlopen, mock.patch.object(narration.time, "sleep"):
            with self.assertRaisesMessage(narration.NarrationError, "injoignable"):
                narration.synthesize("Une phrase.")
        self.assertEqual(urlopen.call_count, len(narration.RETRY_DELAYS) + 1)

    def test_volume_is_normalized(self):
        quiet = array.array("h", [0, 1000, -2000, 500]).tobytes()
        louder = array.array("h", narration.normalize(quiet))
        self.assertEqual(max(map(abs, louder)), round(narration.TARGET_PEAK * 32767))
        self.assertEqual(
            narration.normalize(b"\x00\x00" * 10), b"\x00\x00" * 10
        )  # silence laissé tel quel


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ListeningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)
        self.guide = Guide.objects.create(title="Guide SSH", slug="ssh", estimated_minutes=30)
        GuideDocument.objects.create(guide=self.guide, label="PDF", file="guides/ssh.pdf")
        self.url = reverse("catalogue:guide_listen", args=["ssh"])

    def make_ready(self):
        audio = Narration.objects.create(
            guide=self.guide,
            status=Status.READY,
            version=narration_text.VERSION,
            duration_seconds=120,
        )
        for position in (1, 2):
            section = NarrationSection(
                narration=audio,
                position=position,
                title=f"Chapitre {position}",
                text="Texte.",
                duration_seconds=60,
            )
            section.audio.save(
                f"test-ssh-{position}.mp3", ContentFile(bytes(range(256)) * 40), save=False
            )
            section.save()
        return audio

    def test_request_from_the_page(self):
        self.assertContains(self.client.get(self.url), "Créer la version audio")
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, "Version audio en préparation")
        self.assertEqual(Narration.objects.get().requested_by, self.user)

    def test_audio_supports_range_requests(self):
        self.make_ready()
        audio_url = reverse("catalogue:narration_audio", args=["ssh", 1])
        response = self.client.get(audio_url, HTTP_RANGE="bytes=100-199")
        self.assertEqual(
            (response.status_code, response["Content-Range"]), (206, "bytes 100-199/10240")
        )
        self.assertEqual(len(response.content), 100)
        self.assertEqual(self.client.get(audio_url, HTTP_RANGE="bytes=99999-").status_code, 416)
        full = self.client.get(audio_url)
        self.assertEqual((full.status_code, full["Accept-Ranges"]), (200, "bytes"))
        self.client.logout()
        self.assertEqual(self.client.get(audio_url).status_code, 302)  # réservé aux connectés

    def test_listening_to_every_chapter_marks_the_guide_as_read(self):
        self.make_ready()
        progress_url = reverse("catalogue:listen_progress", args=["ssh"])
        result = self.client.post(progress_url, {"section": 1, "seconds": 30}).json()
        self.assertEqual(result, {"done": 0, "total": 2})
        self.client.post(progress_url, {"section": 1, "seconds": 0, "ended": "1"})
        self.assertFalse(ReadingListItem.objects.filter(finished_at__isnull=False).exists())
        result = self.client.post(progress_url, {"section": 2, "seconds": 0, "ended": "1"}).json()
        self.assertTrue(result["completed"])
        self.assertEqual(result["xp"], 20)  # 10 + 30 // 3, comme une lecture
        self.assertTrue(ReadingListItem.objects.get(guide=self.guide).finished_at)
        self.assertEqual(xp.total_xp(self.user), 20)
        again = self.client.post(progress_url, {"section": 2, "seconds": 0, "ended": "1"}).json()
        self.assertNotIn("completed", again)  # une seule fois
        self.assertEqual(ListeningProgress.objects.get().section, 2)

    def test_progress_ignores_an_unpublished_guide(self):
        self.make_ready()
        Guide.objects.filter(pk=self.guide.pk).update(is_published=False)
        response = self.client.post(
            reverse("catalogue:listen_progress", args=["ssh"]), {"section": 1, "seconds": 0}
        )
        self.assertEqual(response.status_code, 404)

    def test_progress_rejects_bad_input(self):
        self.make_ready()
        progress_url = reverse("catalogue:listen_progress", args=["ssh"])
        self.assertEqual(
            self.client.post(progress_url, {"section": 9, "seconds": 1}).status_code, 400
        )
        self.assertEqual(self.client.post(progress_url, {"section": "x"}).status_code, 400)
        self.assertEqual(self.client.get(progress_url).status_code, 405)

    def test_player_page_resumes_where_the_user_stopped(self):
        audio = self.make_ready()
        ListeningProgress.objects.create(
            user=self.user, narration=audio, section=2, seconds=42.5, sections_done=[1]
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'data-start-section="2"')
        self.assertContains(response, 'data-start-seconds="42.5"')
        self.assertContains(response, "Chapitre 1")
