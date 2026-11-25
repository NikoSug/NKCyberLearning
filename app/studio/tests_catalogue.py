"""Atelier de création : accueil, contenus du catalogue (guide, référentiel, programme,
certification, parcours),
QCM et lexique d'un guide, versions audio enregistrées."""

import io
import shutil
import tempfile
from unittest import mock

import lameenc
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfWriter

from accounts.models import AUTHORS_GROUP, CONTENT_MANAGERS_GROUP
from catalogue import glossary, quizgen
from catalogue.models import (
    AudioUpload,
    Certification,
    Glossary,
    GlossaryEntry,
    Guide,
    GuideCategory,
    GuideDocument,
    GuideSection,
    Narration,
    QuizDraft,
    QuizDraftQuestion,
    ReadingListItem,
    ReadingPath,
    Referential,
    Requirement,
    RequirementProgress,
)

from . import ai, audio, grid

User = get_user_model()
PASSWORD = "Un-mot-de-passe-solide-42!"


def user(name, *groups):
    account = User.objects.create_user(name, password=PASSWORD)
    for group in groups:
        account.groups.add(Group.objects.get_or_create(name=group)[0])
    return account


def pdf_bytes(pages=4):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def mp3_bytes(seconds=2.0, rate=24000):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(64)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(1)
    return bytes(encoder.encode(b"\x00\x01" * int(rate * seconds)) + encoder.flush())


class StudioCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.category = GuideCategory.objects.create(name="Réseau", slug="reseau")
        self.guide = Guide.objects.create(
            title="Pare-feu",
            slug="pare-feu",
            category=self.category,
            summary="Filtrer les flux réseau entre zones de confiance.",
        )
        GuideDocument(guide=self.guide, label="PDF").file.save(
            "pare-feu.pdf", ContentFile(b"%PDF-1.4"), save=True
        )
        self.author = user("redac", AUTHORS_GROUP)
        self.manager = user("gest", CONTENT_MANAGERS_GROUP)
        self.client.force_login(self.author)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)


class HubTests(StudioCase):
    def test_cards_by_role(self):
        page = self.client.get(reverse("studio:home"))
        for label in (
            "Guides",
            "Référentiels",
            "Programmes d&#x27;examen",
            "Certifications",
            "Parcours sur mesure",
            "Cours",
            "Podcasts",
            "QCM des guides",
            "Lexiques",
            "Versions audio enregistrées",
        ):
            self.assertContains(page, label)
        self.assertNotContains(page, "Domaines")  # réservé aux gestionnaires
        self.assertEqual(self.client.get(reverse("studio:list", args=["domaine"])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("studio:edit_new", args=["domaine"])).status_code, 403
        )
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("studio:home")), "Domaines")
        self.assertContains(
            self.client.get(reverse("studio:list", args=["guide"]), {"etat": "publies"}), "Pare-feu"
        )
        self.client.force_login(user("lea"))
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 403)


class GuideTests(StudioCase):
    def test_add_a_guide_then_publish(self):
        response = self.client.post(
            reverse("studio:edit_new", args=["guide"]),
            {
                "title": "Sécuriser le Wi-Fi",
                "summary": "",
                "category": self.category.pk,
                "level": "1-debutant",
                "language": "fr",
                "estimated_minutes": 30,
            },
        )
        guide = Guide.objects.get(title="Sécuriser le Wi-Fi")
        self.assertRedirects(response, reverse("studio:edit", args=["guide", guide.pk]))
        self.assertEqual(
            (guide.is_published, guide.editorial_status, guide.author),
            (False, "redaction", self.author),
        )
        self.assertFalse(
            self.client.get(reverse("catalogue:guide_detail", args=[guide.slug])).status_code == 200
        )
        action = reverse("studio:action", args=["guide", guide.pk])
        self.client.post(action, {"action": "soumettre"})
        self.assertEqual(
            Guide.objects.get(pk=guide.pk).editorial_status, "redaction"
        )  # ni PDF ni résumé
        self.client.post(
            reverse("studio:guide_document", args=[guide.pk]),
            {
                "label": "Guide",
                "file": SimpleUploadedFile("wifi.pdf", pdf_bytes(8), "application/pdf"),
            },
        )
        guide.refresh_from_db()
        self.assertEqual((guide.page_count, guide.documents.count()), (8, 1))
        self.assertGreater(guide.estimated_minutes, 0)
        self.client.post(
            reverse("studio:edit", args=["guide", guide.pk]),
            {
                "title": "Sécuriser le Wi-Fi",
                "summary": "Comment protéger un réseau sans fil d'entreprise pas à pas.",
                "category": self.category.pk,
                "level": "1-debutant",
                "language": "fr",
                "estimated_minutes": 20,
            },
        )
        self.client.post(action, {"action": "soumettre"})
        self.assertEqual(Guide.objects.get(pk=guide.pk).editorial_status, "a_relire")
        self.client.post(
            reverse("studio:edit", args=["guide", guide.pk]),
            {
                "title": "Changé",
                "category": self.category.pk,
                "level": "1-debutant",
                "language": "fr",
                "estimated_minutes": 20,
            },
        )
        self.assertEqual(
            Guide.objects.get(pk=guide.pk).title, "Sécuriser le Wi-Fi"
        )  # soumis : plus modifiable
        self.client.force_login(self.manager)
        self.assertContains(
            self.client.get(reverse("studio:home")), "Sécuriser le Wi-Fi"
        )  # à relire
        self.client.post(action, {"action": "publier"})
        guide.refresh_from_db()
        self.assertEqual((guide.is_published, guide.editorial_status), (True, ""))

    def test_others_cannot_see_a_draft_and_drafts_can_be_deleted(self):
        self.client.post(
            reverse("studio:edit_new", args=["guide"]),
            {
                "title": "Brouillon",
                "category": self.category.pk,
                "level": "1-debutant",
                "language": "fr",
                "estimated_minutes": 30,
            },
        )
        guide = Guide.objects.get(title="Brouillon")
        self.client.force_login(user("autre", AUTHORS_GROUP))
        self.assertEqual(
            self.client.get(reverse("studio:edit", args=["guide", guide.pk])).status_code, 403
        )
        self.client.force_login(self.author)
        self.client.post(
            reverse("studio:action", args=["guide", guide.pk]), {"action": "supprimer"}
        )
        self.assertFalse(Guide.objects.filter(pk=guide.pk).exists())
        self.client.post(
            reverse("studio:action", args=["guide", self.guide.pk]), {"action": "supprimer"}
        )
        self.assertTrue(
            Guide.objects.filter(pk=self.guide.pk).exists()
        )  # publié : jamais supprimé d'ici


class GridTests(TestCase):
    def test_round_trip(self):
        text = (
            "## Gouvernance\n- R1 : Nommer un responsable [10 %]\n  - Vérifier la lettre de"
            " mission\n  > Signée par la direction\n- Former les équipes\n\n## Technique\n- A.5.1 :"
            " Cloisonner le réseau"
        )
        rows = grid.parse(text)
        self.assertEqual(
            [r.kind for r in rows],
            ["section", "requirement", "requirement", "section", "requirement"],
        )
        self.assertEqual(
            (rows[1].ref, rows[1].text, rows[1].weight, rows[1].theme),
            ("R1", "Nommer un responsable", "10 %", "Gouvernance"),
        )
        self.assertEqual(rows[1].checks[0].guidance, "Signée par la direction")
        self.assertEqual(rows[4].ref, "A.5.1")
        self.assertEqual(grid.count(rows), 6)


class ReferentialTests(StudioCase):
    def create(self, key, name):
        self.client.post(
            reverse("studio:edit_new", args=[key]),
            {
                "name": name,
                "kind": "anssi",
                "description": "Un référentiel d'exigences pour les prestataires.",
                "estimated_minutes": 60,
            },
        )
        return Referential.objects.get(name=name)

    def test_grid_keeps_learner_progress(self):
        referential = self.create("referentiel", "Exigences PASSI")
        self.assertEqual((referential.kind, referential.is_published), ("anssi", False))
        url = reverse("studio:grid_save", args=["referentiel", referential.pk])
        self.client.post(
            url,
            {
                "grid": "- R1 : Nommer un responsable\n- R2 : Former les auditeurs",
                "action": "texte",
            },
        )
        first = referential.requirements.get(ref="R1")
        RequirementProgress.objects.create(user=self.author, requirement=first, status="fait")
        self.client.post(
            url, {"grid": "- R1 : Nommer un responsable sécurité\n- R3 : Tenir un registre"}
        )
        self.assertEqual(
            referential.requirements.get(ref="R1").pk, first.pk
        )  # même référence : progression gardée
        self.assertEqual(
            sorted(referential.requirements.values_list("ref", flat=True)), ["R1", "R3"]
        )
        self.assertTrue(RequirementProgress.objects.filter(requirement=first).exists())
        self.assertIn("R1 : Nommer un responsable sécurité", grid.to_text(referential))

    def test_exam_program_lines_are_domains(self):
        program = self.create("programme", "Programme CISSP")
        self.assertEqual(program.kind, "exam")
        self.client.post(
            reverse("studio:grid_save", args=["programme", program.pk]),
            {"grid": "- Domaine 1 - Sécurité et risques [15 %]\n- Domaine 2 - Actifs [10 %]"},
        )
        self.assertEqual(list(program.requirements.values_list("kind", "weight")),
                         [(Requirement.Kind.DOMAIN, "15 %"), (Requirement.Kind.DOMAIN, "10 %")])
        self.assertEqual(
            self.client.get(reverse("studio:edit", args=["referentiel", program.pk])).status_code,
            404,
        )

    def test_citations(self):
        referential = self.create("referentiel", "Exigences PDIS")
        url = reverse("studio:citations_save", args=[referential.pk])
        self.client.post(url, {"action": "ajouter", "code": "[3]", "guide": self.guide.pk})
        citation = referential.citations.get()
        self.assertEqual((citation.title, citation.guide), ("Pare-feu", self.guide))
        self.client.post(url, {"action": "relier", "citation": citation.pk, "guide": ""})
        self.assertIsNone(referential.citations.get().guide)
        self.client.post(url, {"action": "retirer", "citation": citation.pk})
        self.assertFalse(referential.citations.exists())


class CertificationTests(StudioCase):
    def test_certification_with_links(self):
        referential = Referential.objects.create(name="Programme", slug="programme", kind="exam")
        self.client.post(
            reverse("studio:edit_new", args=["certification"]),
            {
                "name": "Expert pare-feu",
                "acronym": "EPF",
                "kind": "individual",
                "description": "Une certification courte.",
                "referentials": [referential.pk],
                "guide_categories": [self.category.pk],
            },
        )
        certification = Certification.objects.get(name="Expert pare-feu")
        self.assertEqual(
            (list(certification.referentials.all()), list(certification.guide_categories.all())),
            ([referential], [self.category]),
        )
        self.client.post(
            reverse("studio:action", args=["certification", certification.pk]),
            {"action": "soumettre"},
        )
        self.assertEqual(
            Certification.objects.get().editorial_status, "redaction"
        )  # description trop courte


class PathTests(StudioCase):
    def test_custom_path(self):
        other = Guide.objects.create(title="VPN", slug="vpn", category=self.category)
        self.client.post(
            reverse("studio:edit_new", args=["parcours"]),
            {
                "title": "Sécuriser son réseau",
                "description": "Deux étapes.",
                "level": "1-debutant",
                "category": self.category.pk,
            },
        )
        path = ReadingPath.objects.get()
        url = reverse("studio:path_step", args=[path.pk])
        for guide in (self.guide, other):
            self.client.post(
                url, {"action": "ajouter", "guide": guide.pk, "note": f"Étape {guide.title}"}
            )
        step = path.steps.get(guide=other)
        self.client.post(url, {"action": "monter", "step": step.pk})
        self.assertEqual(
            list(path.steps.values_list("guide__slug", flat=True)), ["vpn", "pare-feu"]
        )
        self.client.force_login(self.manager)
        self.client.post(
            reverse("studio:action", args=["parcours", path.pk]), {"action": "publier"}
        )
        self.client.force_login(self.author)
        page = self.client.get(reverse("catalogue:custom_path", args=[path.slug]))
        self.assertContains(page, "Étape VPN")
        self.assertContains(self.client.get(reverse("catalogue:path_list")), "Sécuriser son réseau")
        self.client.post(reverse("catalogue:custom_path_add", args=[path.slug]))
        self.assertEqual(ReadingListItem.objects.filter(user=self.author).count(), 2)


class QuizTests(StudioCase):
    def test_write_a_quiz(self):
        response = self.client.post(
            reverse("studio:create", args=["qcm"]), {"guide": self.guide.pk}
        )
        draft = QuizDraft.objects.get()
        self.assertRedirects(response, reverse("studio:qcm_edit", args=[draft.pk]))
        self.assertEqual((draft.status, draft.author), ("redaction", self.author))
        url = reverse("studio:qcm_question", args=[draft.pk])
        self.client.post(
            url,
            {
                "serie": 2,
                "text": "Que filtre un pare-feu ?",
                "correct": "Les flux",
                "wrong_1": "Les fichiers",
                "wrong_2": "Les mots de passe",
            },
        )
        self.assertEqual(draft.questions.get().series, 2)
        sections = [
            GuideSection(title=f"Partie {n}", text="Le pare-feu filtre les flux réseau. " * 40)
            for n in range(6)
        ]
        found = [
            {
                "text": f"Question {n} sur le filtrage ?",
                "choices": ["A", "B", "C", "D"],
                "explanation": "",
                "source": "",
            }
            for n in range(5)
        ]
        with mock.patch("catalogue.assistant.sections", return_value=sections), \
                mock.patch.object(quizgen, "generate_series", return_value=found), \
                mock.patch.object(quizgen, "cross_check", return_value=[True] * 5):
            self.client.post(reverse("studio:qcm_ai", args=[draft.pk]), {"serie": 1})
        self.assertEqual(draft.questions.filter(series=1).count(), 5)
        self.client.post(reverse("studio:qcm_action", args=[draft.pk]), {"action": "soumettre"})
        self.assertEqual(QuizDraft.objects.get().status, "redaction")  # séries incomplètes
        for number in range(2, 7):
            for n in range(5):
                QuizDraftQuestion.objects.create(
                    draft=draft,
                    series=number,
                    position=n + 10,
                    text=f"Q{number}-{n} ?",
                    choices=["A", "B", "C"],
                )
        self.client.post(reverse("studio:qcm_action", args=[draft.pk]), {"action": "soumettre"})
        self.assertEqual(QuizDraft.objects.get().status, "a_relire")
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse("catalogue:review_detail", args=[draft.pk])).status_code, 200
        )


class GlossaryTests(StudioCase):
    def test_write_a_glossary(self):
        self.client.post(reverse("studio:create", args=["lexique"]), {"guide": self.guide.pk})
        item = Glossary.objects.get()
        self.assertEqual((item.status, item.author), ("redaction", self.author))
        action = reverse("catalogue:glossary_review_action", args=[item.pk])
        for n in range(5):
            self.client.post(
                action,
                {
                    "action": "ajouter",
                    "term": f"SIGLE{n}",
                    "expansion": "",
                    "definition": f"Définition numéro {n} assez longue pour être acceptée.",
                },
            )
        self.assertEqual(item.entries.count(), 5)
        self.client.post(action, {"action": "soumettre"})
        self.assertEqual(Glossary.objects.get().status, "a_relire")
        self.client.force_login(self.manager)
        self.client.post(action, {"action": "renvoyer"})
        self.client.force_login(self.author)
        self.client.post(action, {"action": "generer"})
        item.refresh_from_db()
        self.assertEqual((item.status, item.author), ("attente", self.author))
        entry = glossary.check_entry(
            {
                "terme": "PSSI",
                "forme_developpee": "Politique de sécurité",
                "definition": "Document qui fixe les règles de sécurité de l'organisation.",
            },
            "La PSSI fixe les règles.",
        )
        with mock.patch.object(
            glossary, "parts", return_value=[quizgen.Part("Partie", "La PSSI fixe les règles.")]
        ), mock.patch.object(glossary, "analyse_part", return_value=[entry]):
            glossary.generate(glossary.take_next())
        self.assertEqual(Glossary.objects.get().status, "redaction")  # revient dans l'atelier

    def test_other_author_cannot_open(self):
        item = Glossary.objects.create(guide=self.guide, author=self.manager, status="redaction")
        self.assertEqual(
            self.client.get(reverse("catalogue:glossary_review", args=[item.pk])).status_code, 403
        )


class AudioTests(StudioCase):
    def test_mp3_duration(self):
        self.assertAlmostEqual(audio.mp3_duration(mp3_bytes(3.0)), 3.0, delta=0.2)
        self.assertIsNone(audio.mp3_duration(b"pas un mp3" * 1000))

    def test_recorded_version_replaces_the_synthetic_voice(self):
        narration = Narration.objects.create(
            guide=self.guide, status=Narration.Status.READY, voice="Kyutai TTS"
        )
        self.client.post(reverse("studio:create", args=["audio"]), {"guide": self.guide.pk})
        upload = AudioUpload.objects.get()
        url = reverse("studio:audio_part", args=[upload.pk])
        for n in (1, 2):
            self.client.post(
                url,
                {
                    "action": "ajouter",
                    "title": f"Chapitre {n}",
                    "audio": SimpleUploadedFile(f"c{n}.mp3", mp3_bytes(2.0), "audio/mpeg"),
                },
            )
        self.client.post(
            url,
            {
                "action": "ajouter",
                "title": "Faux",
                "audio": SimpleUploadedFile("x.mp3", b"rien" * 100),
            },
        )
        self.assertEqual(upload.parts.count(), 2)
        part = upload.parts.first()
        self.assertEqual(
            self.client.get(reverse("studio:audio_file", args=[upload.pk, part.pk])).status_code,
            200,
        )
        self.client.post(reverse("studio:audio_action", args=[upload.pk]), {"action": "soumettre"})
        self.client.force_login(self.manager)
        self.client.post(reverse("studio:audio_action", args=[upload.pk]), {"action": "publier"})
        narration.refresh_from_db()
        self.assertEqual((narration.status, narration.voice, narration.sections.count()),
                         (Narration.Status.READY, "Enregistrement de redac", 2))
        self.assertAlmostEqual(narration.duration_seconds, 4.0, delta=0.4)
        self.client.force_login(self.author)
        self.assertContains(
            self.client.get(reverse("catalogue:guide_listen", args=[self.guide.slug])),
            "voix humaine",
        )


class SummaryTests(StudioCase):
    def test_document_summary_prompt(self):
        with mock.patch.object(
            ai, "source_text", return_value="Le guide explique le filtrage. " * 20
        ):
            messages = ai.build_messages(
                "resumer", "fiche", title="Pare-feu", source=f"guide:{self.guide.pk}"
            )
        self.assertIn("présentation de ce document", messages[1]["content"])
        with self.assertRaisesMessage(ai.AIError, "Ajoutez d'abord le PDF"):
            ai.build_messages("resumer", "fiche", title="Vide", source="guide:0")


class FeedbackTests(StudioCase):
    """Retours de l'utilisateur : contenus publiés modifiables, domaines, lexiques existants, liens
    de retour."""

    def test_published_podcast_can_be_edited_then_rerecorded(self):
        from podcasts.models import Episode, Series
        series = Series.objects.create(
            category=self.category,
            title="Série publiée",
            slug="serie-publiee",
            status=Series.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        script = [{"speaker": n % 2, "text": f"Réplique {n}. " + "mot " * 50} for n in range(12)]
        episode = Episode.objects.create(series=series, position=1, title="Épisode", script=script)
        episode.audio.save("e1.mp3", ContentFile(mp3_bytes(1.0)), save=True)
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse("podcasts:review_series", args=[series.pk])).status_code, 200
        )
        body = "\n".join(
            f"{'Clara' if n % 2 == 0 else 'Hugo'} : Réplique corrigée {n}. " + "mot " * 50
            for n in range(12)
        )
        self.client.post(
            reverse("podcasts:review_episode", args=[episode.pk]),
            {"title": "Épisode", "body": body},
        )
        episode.refresh_from_db()
        self.assertTrue(episode.needs_recording)
        self.client.post(
            reverse("podcasts:review_series_action", args=[series.pk]), {"action": "reenregistrer"}
        )
        series.refresh_from_db()
        self.assertEqual(series.status, Series.Status.TO_RECORD)
        self.assertContains(
            self.client.get(reverse("podcasts:podcast_list")), "Série publiée"
        )  # reste écoutable

    def test_published_course_can_be_edited(self):
        from courses.models import Course, Lesson
        course = Course.objects.create(
            kind="theme",
            category=self.category,
            title="Cours publié",
            slug="cours-publie",
            status=Course.Status.PUBLISHED,
        )
        lesson = Lesson.objects.create(
            course=course, position=1, title="Leçon", body="Texte. " * 30
        )
        self.client.force_login(self.manager)
        self.client.post(reverse("courses:review_lesson", args=[lesson.pk]),
                         {"title": "Leçon corrigée", "body": "Un texte corrigé. " * 20})
        self.assertEqual(Lesson.objects.get().title, "Leçon corrigée")
        self.client.force_login(self.author)  # un rédacteur ne modifie pas ce qui est publié
        self.client.post(
            reverse("courses:review_lesson", args=[lesson.pk]),
            {"title": "Autre", "body": "x " * 80},
        )
        self.assertEqual(Lesson.objects.get().title, "Leçon corrigée")

    def test_domain_members(self):
        other = GuideCategory.objects.create(name="Cloud", slug="cloud")
        loose = Guide.objects.create(title="Sans domaine", slug="sans-domaine")
        certification = Certification.objects.create(name="Expert", slug="expert")
        self.client.force_login(self.manager)
        url = reverse("studio:domain_members", args=[self.category.pk])
        self.client.post(url, {"action": "ajouter-guide", "guide": loose.pk})
        self.assertEqual(Guide.objects.get(pk=loose.pk).category, self.category)
        self.client.post(url, {"action": "deplacer-guide", "guide": loose.pk, "vers": other.pk})
        self.assertEqual(Guide.objects.get(pk=loose.pk).category, other)
        self.client.post(url, {"action": "lier-certification", "certification": certification.pk})
        self.assertEqual(list(certification.guide_categories.all()), [self.category])
        page = self.client.get(reverse("studio:edit", args=["domaine", self.category.pk]))
        self.assertContains(page, "Guides du domaine")
        self.assertContains(page, "Pare-feu")

    def test_existing_glossary_opens_to_be_completed(self):
        item = Glossary.objects.create(guide=self.guide, status="publie")
        GlossaryEntry.objects.create(
            glossary=item, term="PSSI", key="pssi", definition="Politique de sécurité."
        )
        response = self.client.post(
            reverse("studio:create", args=["lexique"]), {"guide": self.guide.pk}
        )
        self.assertContains(response, "un gestionnaire de contenu peut le compléter")  # rédacteur
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("studio:create", args=["lexique"]), {"guide": self.guide.pk}
        )
        self.assertRedirects(response, reverse("catalogue:glossary_review", args=[item.pk]))
        self.client.post(
            reverse("catalogue:glossary_review_action", args=[item.pk]),
            {
                "action": "ajouter",
                "term": "EDR",
                "definition": "Outil de détection des attaques sur les postes.",
            },
        )
        self.assertEqual(item.entries.count(), 2)
        self.assertEqual(Glossary.objects.get().status, "publie")  # complété sans être dépublié

    def test_back_links_open_the_right_tab(self):
        from . import sources
        self.assertEqual(
            sources.list_url("cours", "publie"),
            reverse("studio:list", args=["cours"]) + "?etat=publies",
        )
        self.assertEqual(sources.bucket_for("a_relire"), "relecture")
        self.client.post(
            reverse("studio:edit_new", args=["guide"]),
            {
                "title": "Brouillon",
                "category": self.category.pk,
                "level": "1-debutant",
                "language": "fr",
                "estimated_minutes": 30,
            },
        )
        guide = Guide.objects.get(title="Brouillon")
        self.assertContains(
            self.client.get(reverse("studio:edit", args=["guide", guide.pk])), "?etat=brouillons"
        )
