"""Pré-relecteur IA (modèle simulé) : avis, notes, régénération des questions ratées, réglage,
écrans de relecture."""

import re
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP, User
from courses import coursegen
from courses import tests as course_tests
from courses.models import Course

from . import generation, narration_text, prereview, quizgen
from . import tests_quizgen
from .models import Guide, GuideCategory, GuideDocument, QuizDraft, QuizDraftQuestion

Verdict = QuizDraftQuestion.Prereview
Decision = QuizDraftQuestion.Decision
GOOD = {"verdict": "bonne", "probleme": "aucun", "note": ""}
DOUBT = {
    "verdict": "douteuse",
    "probleme": "formulation_floue",
    "note": "le guide parle d'autre chose",
}
FAILED = {
    "verdict": "ratee",
    "probleme": "reponse_non_justifiee",
    "note": "le guide dit le contraire",
}


def with_verdicts(verdict, base=tests_quizgen.fake_chat):
    """Modèle simulé : `verdict(question)` pour la pré-relecture, `base` pour tout le reste."""
    calls, models = [], set()

    def chat(messages, schema, temperature=0.4, model=None):
        if "avis" in schema["properties"]:
            questions = re.findall(r"^(\d+)\. (.+)$", messages[-1]["content"], re.M)
            calls.append(len(questions))
            models.add(model)
            answer = {"avis": [{"numero": int(n), **verdict(text)} for n, text in questions]}
            if "problemes_lecon" in schema["properties"]:
                answer["problemes_lecon"] = [
                    "la leçon parle de 8 caractères, le guide en recommande 12"
                ]
            return answer
        return base(messages, schema, temperature)
    chat.calls, chat.models = calls, models
    return chat


def first_fails_once():
    """La toute première question est ratée, la deuxième douteuse, les autres (et la remplaçante)
    bonnes."""
    seen = []

    def verdict(text):
        if len(seen) < 2 and text not in seen:
            seen.append(text)
        return (
            FAILED
            if seen and text == seen[0]
            else DOUBT if len(seen) > 1 and text == seen[1] else GOOD
        )
    return verdict


class MediaMixin:
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.guide = Guide.objects.create(
            title="Guide pare-feu", slug="pare-feu", anssi_id="pare-feu"
        )
        document = GuideDocument(guide=self.guide, label="PDF")
        document.file.save("pare-feu.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)
        self.manager = User.objects.create_user("gestion", is_staff=True)
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)


@override_settings(PREREVIEW_ENABLED=True, AI_ENABLED=True)
@mock.patch.object(
    prereview, "DUPLICATE_RATIO", 0.95
)  # questions simulées tirées au hasard : pas de faux doublons
class QuizPrereviewTests(MediaMixin, TestCase):
    def generate(self, chat, checkpoint=None, draft=None):
        with mock.patch.object(
            narration_text, "extract_sections", return_value=tests_quizgen.SECTIONS
        ), mock.patch.object(quizgen, "chat", side_effect=chat):
            return quizgen.generate(draft or quizgen.take_next(), checkpoint=checkpoint)

    def test_regenerated_question_does_not_keep_the_old_diagram(self):
        draft = quizgen.request(self.guide)
        question = QuizDraftQuestion.objects.create(
            draft=draft,
            series=4,
            text="Ancienne ?",
            choices=["A", "B", "C", "D"],
            diagram="A[Automate] --> B[Bureau]",
        )
        item = {
            "text": "Nouvelle ?",
            "choices": ["W", "X", "Y", "Z"],
            "explanation": "",
            "source": "R1",
            "diagram": "",
        }
        with mock.patch.object(quizgen, "generate_series", return_value=[item]), \
                mock.patch.object(quizgen, "cross_check", return_value=[True]):
            self.assertTrue(
                prereview._regenerate(draft, quizgen.Part("P", "Texte."), 4, question, "faux", [])
            )
        question.refresh_from_db()
        self.assertEqual(
            (question.text, question.diagram), ("Nouvelle ?", "")
        )  # le schéma suit la question

    def test_failed_question_is_regenerated_and_doubtful_one_noted(self):
        quizgen.request(self.guide)
        chat = with_verdicts(first_fails_once())
        draft = self.generate(chat)
        self.assertEqual(draft.status, QuizDraft.Status.REVIEW)  # jamais publié : relecture humaine
        self.assertIsNotNone(draft.prereviewed_at)
        self.assertEqual(draft.questions.count(), 30)
        regenerated = draft.questions.get(prereview_attempts=1)
        self.assertEqual(regenerated.prereview, Verdict.REGENERATED)
        self.assertIn("Régénérée par le pré-relecteur", regenerated.prereview_note)
        self.assertIn("le guide dit le contraire", regenerated.prereview_note)
        doubtful = draft.questions.get(prereview=Verdict.DOUBT)
        self.assertIn("formulation floue : le guide parle d'autre chose", doubtful.prereview_note)
        self.assertEqual(draft.questions.filter(prereview=Verdict.OK).count(), 28)
        self.assertFalse(draft.questions.exclude(decision=Decision.PENDING).exists())
        self.assertEqual(
            chat.calls, [5, 1, 5, 5, 5, 5, 5]
        )  # série 1, la remplaçante, puis les séries 2 à 6

        self.client.force_login(self.manager)
        page = self.client.get(
            reverse("catalogue:review_detail", args=[draft.pk]) + f"?q={doubtful.pk}"
        )
        self.assertContains(page, "Douteuse selon le pré-relecteur")
        self.assertContains(page, "le guide parle d&#x27;autre chose")
        self.assertContains(
            page, "1 question douteuse à regarder de près, 1 régénérée d&#x27;office."
        )
        listing = self.client.get(reverse("catalogue:review_list") + "?type=qcm")
        self.assertContains(listing, "1 à vérifier")
        draft.questions.update(cross_check=True)
        self.client.post(
            reverse("catalogue:review_draft", args=[draft.pk]), {"action": "accepter-verifiees"}
        )
        doubtful.refresh_from_db()
        self.assertEqual(doubtful.decision, Decision.PENDING)  # la douteuse attend un humain
        self.assertEqual(draft.questions.filter(decision=Decision.ACCEPTED).count(), 29)

    @override_settings(PREREVIEW_LLM_MODEL="juge:14b")
    def test_verdicts_come_from_the_prereview_model(self):
        quizgen.request(self.guide)
        chat = with_verdicts(lambda text: GOOD)
        self.generate(chat)
        self.assertEqual(
            chat.models, {"juge:14b"}
        )  # la génération garde son modèle (pas de `model` passé)

    def test_regenerations_are_limited(self):
        quizgen.request(self.guide)
        draft = self.generate(with_verdicts(lambda text: FAILED))
        attempts = list(draft.questions.values_list("prereview_attempts", flat=True))
        self.assertEqual(sum(attempts), prereview.REGENERATION_BUDGET)
        self.assertLessEqual(max(attempts), prereview.MAX_REGENERATIONS)
        self.assertEqual(
            draft.questions.filter(prereview=Verdict.DOUBT).count(), 30
        )  # signalées, pas rejetées
        worst = draft.questions.filter(prereview_attempts=prereview.MAX_REGENERATIONS).first()
        self.assertIn("ratée selon le pré-relecteur (après régénération)", worst.prereview_note)
        self.assertEqual(draft.questions.filter(decision=Decision.REJECTED).count(), 0)

    def test_disabled_from_dashboard_or_settings(self):
        self.client.force_login(self.manager)
        self.client.post(
            reverse("catalogue:generation_action", args=["prerelu"]), {"action": "desactiver"}
        )
        self.assertFalse(prereview.enabled())
        self.assertContains(self.client.get(reverse("catalogue:generation_dashboard")), "L'activer")
        quizgen.request(self.guide)
        chat = with_verdicts(lambda text: FAILED)
        draft = self.generate(chat)
        self.assertEqual(chat.calls, [])
        self.assertIsNone(draft.prereviewed_at)
        self.assertFalse(draft.questions.exclude(prereview=Verdict.NONE).exists())
        self.client.post(
            reverse("catalogue:generation_action", args=["prerelu"]), {"action": "activer"}
        )
        self.assertTrue(prereview.enabled())
        with override_settings(PREREVIEW_ENABLED=False):
            self.assertFalse(prereview.enabled())
            self.assertContains(
                self.client.get(reverse("catalogue:generation_dashboard")), "PREREVIEW_ENABLED=0"
            )
        with override_settings(AI_ENABLED=False):
            self.assertFalse(prereview.enabled())

    def test_ai_failure_leaves_draft_to_humans(self):
        quizgen.request(self.guide)

        def broken(messages, schema, temperature=0.4, model=None):
            if "avis" in schema["properties"]:
                raise quizgen.GenerationError("modèle d'IA injoignable")
            return tests_quizgen.fake_chat(messages, schema, temperature)
        draft = self.generate(broken)
        self.assertEqual(draft.status, QuizDraft.Status.REVIEW)
        self.assertIn("pré-relecture interrompue", draft.prereview_notes[0])
        self.assertFalse(draft.questions.exclude(prereview=Verdict.NONE).exists())

    def test_pause_during_prereview_resumes_where_it_stopped(self):
        draft = quizgen.request(self.guide)
        chat = with_verdicts(lambda text: GOOD)
        calls = []

        def checkpoint():
            calls.append(1)
            if len(calls) == 8:  # 6 séries générées, série 1 pré-relue, pause avant la série 2
                raise generation.Paused
        with self.assertRaises(generation.Paused):
            self.generate(chat, checkpoint=checkpoint, draft=quizgen.take_next())
        quizgen.suspend(draft)
        self.assertEqual(chat.calls, [5])
        draft = self.generate(chat)
        self.assertEqual(chat.calls, [5, 5, 5, 5, 5, 5])  # la série 1 n'est pas relue deux fois
        self.assertEqual(draft.questions.filter(prereview=Verdict.OK).count(), 30)

    def test_author_drafts_and_human_decisions_are_left_alone(self):
        draft = quizgen.request(self.guide)
        draft.author = self.manager
        draft.save()
        chat = with_verdicts(lambda text: FAILED)
        self.generate(chat)
        self.assertEqual(chat.calls, [])


class DuplicateTests(SimpleTestCase):
    def test_duplicates_are_noted_without_ai(self):
        self.assertEqual(
            prereview.duplicate_of(
                "Quelle longueur minimale pour un mot de passe ?",
                ["Autre chose", "Quelle longueur minimale pour les mots de passe ?"],
            ),
            1,
        )
        self.assertIsNone(
            prereview.duplicate_of(
                "Quel algorithme de chiffrement ?", ["Qui gère les sauvegardes ?"]
            )
        )


@override_settings(PREREVIEW_ENABLED=True, AI_ENABLED=True)
class CoursePrereviewTests(MediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = GuideCategory.objects.create(name="Fondamentaux", slug="fondamentaux")
        self.guide.category, self.guide.summary = self.category, "Les règles de base."
        self.guide.save()

    def test_course_is_prereviewed_lesson_by_lesson(self):
        course = coursegen.request_guide(self.guide)
        failed = []

        def verdict(text):
            if not failed:
                failed.append(text)
            return FAILED if text == failed[0] else GOOD
        chat = with_verdicts(verdict, base=course_tests.fake_chat)
        with mock.patch.object(
            narration_text, "extract_sections", return_value=course_tests.SECTIONS
        ), mock.patch.object(coursegen.quizgen, "chat", side_effect=chat):
            course = coursegen.generate(coursegen.take_next())
        self.assertEqual(course.status, Course.Status.REVIEW)
        self.assertIsNotNone(course.prereviewed_at)
        lessons = len(course.plan)
        self.assertEqual(
            course.prereview["done"], [str(n) for n in range(1, lessons + 1)] + ["examen"]
        )
        self.assertEqual(course.prereview["regenerated"], 1)
        self.assertFalse(course.questions.filter(text=failed[0]).exists())
        notes = prereview.course_notes(course, "1")
        self.assertIn("Leçon 1 : la leçon parle de 8 caractères, le guide en recommande 12", notes)
        self.assertTrue(
            any(
                "Leçon 1, question 1 régénérée" in n and "le guide dit le contraire" in n
                for n in notes
            )
        )

        self.client.force_login(self.manager)
        page = self.client.get(reverse("courses:review_course", args=[course.pk]) + "?etape=1")
        self.assertContains(page, "Pré-relecteur IA")
        self.assertContains(page, "le guide en recommande 12")

        coursegen.reset(course)
        self.assertEqual(course.prereview, {})
