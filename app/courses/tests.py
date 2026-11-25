"""Cours : génération (modèle simulé), relecture, publication, parcours de l'apprenant (leçons,
quiz, examen)."""

import itertools
import json
import random
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP
from catalogue import generation, narration_text
from catalogue.models import Guide, GuideCategory, GuideDocument
from progression.models import XPEvent

from . import coursegen
from .models import Course, CourseQuestion, ExamAttempt
from .templatetags.course_text import lesson_html

User = get_user_model()
Status = Course.Status
WORDS = [
    "pare-feu",
    "sauvegarde",
    "mot de passe",
    "chiffrement",
    "journal",
    "mise à jour",
    "hameçonnage",
    "réseau",
    "certificat",
    "annuaire",
    "routeur",
    "messagerie",
    "badge",
    "tunnel",
    "serveur",
    "navigateur",
]
COUNTER = itertools.count()
SECTIONS = [
    narration_text.Section(f"Chapitre {n}", f"Texte du chapitre {n} sur la sécurité. " * 120)
    for n in range(1, 5)
]


def fake_questions(count):
    items = []
    for _ in range(count):
        n = next(COUNTER)
        words = random.Random(n).sample(WORDS, 7)  # nosec B311 (données de test)
        items.append(
            {
                "question": " ".join(words).capitalize() + " ?",
                "bonne_reponse": f"Bonne réponse {n}",
                "mauvaises_reponses": [f"Leurre {n} A", f"Leurre {n} B", f"Leurre {n} C"],
                "explication": "Parce que la leçon l'explique.",
            }
        )
    return items


def fake_chat(messages, schema, temperature=0.5):
    props = schema["properties"]
    if "lecons" in props:
        return {
            "titre": "Les bases de la sécurité",
            "presentation": "Un cours pour bien commencer.",
            "lecons": [
                {"titre": f"Leçon {n}", "objectif": f"Objectif {n}", "mots_cles": ["sécurité"]}
                for n in range(1, 4)
            ],
        }
    if "parties" in props:
        return {
            "titre": "Une leçon claire",
            "objectif": "Comprendre l'essentiel",
            "introduction": "Introduction. " * 20,
            "parties": [{"titre": "Partie A", "texte": "Explication simple. " * 30}],
            "exemple": "Une PME qui sauvegarde ses fichiers.",
            "a_retenir": ["Point 1", "Point 2"],
            "questions": fake_questions(4),
        }
    if "presentation" in props:
        return {"presentation": "Résumé du cours.", "questions": fake_questions(10)}
    return {"questions": fake_questions(4)}


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

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def generate(self, course):
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(coursegen.quizgen, "chat", side_effect=fake_chat):
            return coursegen.generate(coursegen.take_next())


class GenerationTests(MediaTestCase):
    def test_theme_course_is_generated_as_a_draft(self):
        course = self.generate(coursegen.request_theme(self.category))
        self.assertEqual(course.status, Status.REVIEW)
        self.assertEqual(course.title, "Les bases de la sécurité")
        self.assertEqual(course.lessons.count(), 3)
        lesson = course.lessons.first()
        self.assertIn("## Partie A", lesson.body)
        self.assertIn("## Un exemple concret", lesson.body)
        self.assertEqual(lesson.key_points, ["Point 1", "Point 2"])
        self.assertIn("Guide d'hygiène", lesson.sources)
        self.assertEqual(lesson.questions.count(), 4)
        self.assertEqual(course.questions.filter(lesson__isnull=True).count(), 10)
        self.assertEqual(coursegen.publish_problems(course), [])
        self.assertEqual(list(coursegen.themes_without_course()), [])

    def test_guide_course_and_resume_after_pause(self):
        course = coursegen.request_guide(self.guide)
        job = coursegen.take_next()
        calls = []

        def checkpoint():
            calls.append(1)
            if len(calls) == 3:  # plan, leçon 1, puis pause avant la leçon 2
                raise generation.Paused
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(coursegen.quizgen, "chat", side_effect=fake_chat):
            with self.assertRaises(generation.Paused):
                coursegen.generate(job, checkpoint=checkpoint)
            coursegen.suspend(job)
            self.assertEqual(Course.objects.get(pk=course.pk).lessons.count(), 1)
            job = coursegen.take_next()
            coursegen.generate(job)
        self.assertEqual(job.status, Status.REVIEW)
        self.assertEqual(job.lessons.count(), len(job.plan))
        self.assertEqual(job.title, "L'essentiel : Guide d'hygiène")

    def test_too_few_questions_fail(self):
        coursegen.request_theme(self.category)
        job = coursegen.take_next()
        broken = lambda messages, schema, temperature=0.5: {
            **fake_chat(messages, schema),
            "questions": [],
        }
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(coursegen.quizgen, "chat", side_effect=broken):
            with self.assertRaisesMessage(coursegen.GenerationError, "question(s) valide(s)"):
                coursegen.generate(job)

    def test_lesson_text_is_escaped(self):
        html = lesson_html("Intro **important** <script>x</script>\n\n## Partie\n- un\n- deux\nFin")
        self.assertIn("<strong>important</strong>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<h2>Partie</h2>", html)
        self.assertIn("<ul><li>un</li><li>deux</li></ul>", html)
        self.assertIn(
            "<ol><li>Copier</li><li>Tester</li></ol>", lesson_html("1. Copier\n2. Tester")
        )
        self.assertIn(
            "un <em>ransomware</em> (rançongiciel)", lesson_html("un *ransomware* (rançongiciel)")
        )
        self.assertIn("2 * 3 = 6", lesson_html("2 * 3 = 6"))

    def test_duplicate_parts_and_numbers_are_removed(self):
        body = coursegen.compose_body(
            {
                "introduction": "Intro.",
                "exemple": "Une PME.",
                "parties": [
                    {"titre": "1. Les bases", "texte": "Texte."},
                    {"titre": "5 points à retenir", "texte": "Doublon."},
                    {"titre": "Exemple concret", "texte": "Doublon."},
                ],
            }
        )
        self.assertIn("## Les bases", body)
        self.assertNotIn("Doublon", body)
        self.assertEqual(body.count("## Un exemple concret"), 1)


class LearnerTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.generate(coursegen.request_theme(self.category))
        self.manager = User.objects.create_user("gestion", password="Un-mot-de-passe-solide-42!")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.user = User.objects.create_user("apprenant", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)

    def publish(self):
        coursegen.publish(self.course, self.manager)
        self.course.refresh_from_db()

    def answer_lesson(self, position, correct=True):
        lesson = self.course.lessons.get(position=position)
        url = reverse("courses:lesson_answer", args=[self.course.slug, position])
        self.client.get(reverse("courses:lesson_quiz", args=[self.course.slug, position]))
        results = []
        for q in lesson.questions.all():
            choice = q.answer if correct else (q.answer + 1) % len(q.choices)
            results.append(self.client.post(url, json.dumps({"question": q.pk, "choice": choice}),
                                            content_type="application/json").json())
        return results, self.client.post(
            reverse("courses:lesson_quiz", args=[self.course.slug, position])
        )

    def test_draft_is_not_visible_until_published(self):
        self.assertEqual(self.client.get(self.course.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("courses:course_list")), self.course.title)
        self.publish()
        self.assertContains(self.client.get(reverse("courses:course_list")), self.course.title)
        # Les propositions ont été mélangées une fois, la bonne réponse reste suivie.
        question = CourseQuestion.objects.filter(course=self.course).first()
        self.assertTrue(question.choices[question.answer].startswith("Bonne réponse"))

    def test_lessons_unlock_one_after_another_then_exam(self):
        self.publish()
        self.assertContains(
            self.client.get(reverse("courses:lesson", args=[self.course.slug, 1])), "À retenir"
        )
        self.assertRedirects(self.client.get(reverse("courses:lesson", args=[self.course.slug, 2])),
                             self.course.get_absolute_url())
        results, response = self.answer_lesson(1, correct=False)
        self.assertFalse(results[0]["correct"])
        self.assertContains(response, "Pas encore")
        results, response = self.answer_lesson(1)
        self.assertTrue(all(r["correct"] for r in results))
        self.assertContains(response, "Sans faute")
        self.assertTrue(
            XPEvent.objects.filter(
                user=self.user, ref=f"lecon:{self.course.lessons.first().pk}"
            ).exists()
        )
        self.assertEqual(
            self.client.get(reverse("courses:lesson", args=[self.course.slug, 2])).status_code, 200
        )
        self.assertRedirects(self.client.get(reverse("courses:exam", args=[self.course.slug])),
                             self.course.get_absolute_url())
        for position in (2, 3):
            self.answer_lesson(position)
        response = self.client.get(reverse("courses:exam", args=[self.course.slug]))
        self.assertNotContains(response, "Une question ?")  # pas d'assistant pendant l'examen
        questions = self.course.questions.filter(lesson__isnull=True)
        response = self.client.post(reverse("courses:exam", args=[self.course.slug]),
                                    {f"q{q.pk}": q.answer for q in questions})
        self.assertContains(response, "Examen réussi")
        self.assertTrue(ExamAttempt.objects.get(user=self.user).passed)
        detail = self.client.get(self.course.get_absolute_url())
        self.assertContains(detail, "🏆")

    def test_first_answer_is_kept(self):
        self.publish()
        lesson = self.course.lessons.first()
        question = lesson.questions.first()
        url = reverse("courses:lesson_answer", args=[self.course.slug, 1])
        self.client.get(reverse("courses:lesson_quiz", args=[self.course.slug, 1]))
        wrong = (question.answer + 1) % len(question.choices)
        self.client.post(
            url,
            json.dumps({"question": question.pk, "choice": wrong}),
            content_type="application/json",
        )
        second = self.client.post(
            url,
            json.dumps({"question": question.pk, "choice": question.answer}),
            content_type="application/json",
        ).json()
        self.assertFalse(second["correct"])  # on ne corrige pas après avoir vu la réponse
        locked = self.client.post(
            reverse("courses:lesson_answer", args=[self.course.slug, 2]),
            json.dumps({"question": question.pk, "choice": 0}),
            content_type="application/json",
        )
        self.assertEqual(locked.status_code, 403)

    def test_review_edit_and_publish(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("courses:review_list")).status_code, 403)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("courses:review_course", args=[self.course.pk]))
        self.assertContains(page, "Leçon 1 sur 3")
        lesson = self.course.lessons.first()
        self.client.post(
            reverse("courses:review_lesson", args=[lesson.pk]),
            {
                "title": "Titre corrigé",
                "objective": "Objectif",
                "body": "Texte corrigé et assez long. " * 10,
                "key_points": "- Premier point\nSecond point",
            },
        )
        lesson.refresh_from_db()
        self.assertEqual(
            (lesson.title, lesson.key_points), ("Titre corrigé", ["Premier point", "Second point"])
        )
        question = lesson.questions.first()
        self.client.post(
            reverse("courses:review_question", args=[question.pk]), {"action": "supprimer"}
        )
        self.client.post(
            reverse("courses:review_question", args=[lesson.questions.first().pk]),
            {"action": "supprimer"},
        )
        self.assertIn("la leçon 1 a moins de 3 questions", coursegen.publish_problems(self.course))
        response = self.client.post(reverse("courses:review_course_action", args=[self.course.pk]),
                                    {"action": "publier"}, follow=True)
        self.assertContains(response, "impossible de publier")
        CourseQuestion.objects.create(
            course=self.course,
            lesson=lesson,
            text="Une question ajoutée pour publier ?",
            choices=["A", "B", "C", "D"],
        )
        self.client.post(
            reverse("courses:review_course_action", args=[self.course.pk]), {"action": "publier"}
        )
        self.assertEqual(Course.objects.get(pk=self.course.pk).status, Status.PUBLISHED)

    def test_generation_dashboard_shows_courses(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(response, 'id="cours"')
        self.client.post(
            reverse("catalogue:generation_action", args=["cours"]),
            {"action": "file", "nombre": "tout"},
        )
        self.assertTrue(Course.objects.filter(kind=Course.Kind.GUIDE, guide=self.guide).exists())
