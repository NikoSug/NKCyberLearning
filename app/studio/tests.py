"""Atelier de rédaction (cours et podcasts écrits par des rédacteurs, aide de l'IA) et gestion des
rôles."""

import json
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AUTHORS_GROUP, CONTENT_MANAGERS_GROUP
from catalogue import assistant, generation, narration_text
from catalogue.models import Guide, GuideCategory, GuideDocument
from courses import coursegen
from courses.models import Course, CourseQuestion, Lesson
from courses.tests import fake_chat as course_chat
from podcasts import podcastgen
from podcasts.models import Episode, Series

from . import ai

User = get_user_model()
PASSWORD = "Un-mot-de-passe-solide-42!"
LONG_BODY = "Une leçon claire sur le pare-feu et le filtrage des flux. " * 12
SECTIONS = [
    narration_text.Section(
        "Guide » Chapitre 1", "Le pare-feu filtre les flux réseau entrants. " * 60
    )
]


def user(name, *groups, **extra):
    account = User.objects.create_user(name, password=PASSWORD, **extra)
    for group in groups:
        account.groups.add(Group.objects.get_or_create(name=group)[0])
    return account


class RoleTests(TestCase):
    def setUp(self):
        self.admin = user("admin", is_superuser=True, is_staff=True)
        self.lea = user("lea")

    def test_roles(self):
        self.assertFalse(self.lea.is_author)
        self.assertTrue(user("redac", AUTHORS_GROUP).is_author)
        self.assertTrue(
            user("gest", CONTENT_MANAGERS_GROUP).is_author
        )  # un gestionnaire rédige aussi
        self.assertTrue(self.admin.is_author)

    def test_only_admins_manage_roles(self):
        url = reverse("accounts:user_list")
        self.client.force_login(user("gest", CONTENT_MANAGERS_GROUP))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url, {"q": "lea"}), "lea")
        action = reverse("accounts:user_action", args=[self.lea.pk])
        self.client.post(action, {"action": "donner-redacteur"})
        self.assertTrue(User.objects.get(pk=self.lea.pk).is_author)
        self.client.post(action, {"action": "donner-gestionnaire"})
        lea = User.objects.get(pk=self.lea.pk)
        self.assertTrue(
            lea.is_content_manager and lea.is_staff
        )  # l'administration du catalogue suit
        self.client.post(action, {"action": "retirer-gestionnaire"})
        self.client.post(action, {"action": "retirer-redacteur"})
        lea = User.objects.get(pk=self.lea.pk)
        self.assertFalse(lea.is_author or lea.is_staff)
        self.client.post(action, {"action": "desactiver"})
        self.assertFalse(User.objects.get(pk=self.lea.pk).is_active)
        self.client.post(
            reverse("accounts:user_action", args=[self.admin.pk]), {"action": "desactiver"}
        )
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)  # pas depuis cette page
        self.assertContains(self.client.get(url, {"role": "inactif"}), "lea")


class StudioTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.category = GuideCategory.objects.create(name="Réseau", slug="reseau")
        guide = Guide.objects.create(
            title="Guide pare-feu", slug="pare-feu", category=self.category
        )
        GuideDocument(guide=guide, label="PDF").file.save(
            "pare-feu.pdf", ContentFile(b"%PDF-1.4"), save=True
        )
        self.author = user("redac", AUTHORS_GROUP)
        self.other = user("autre", AUTHORS_GROUP)
        self.manager = user("gest", CONTENT_MANAGERS_GROUP)
        self.client.force_login(self.author)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def create(
        self, kind="cours", mode="moi", brief="Parler du filtrage des flux entre deux réseaux."
    ):
        return self.client.post(
            reverse("studio:create", args=[kind]),
            {
                "title": "Le pare-feu expliqué",
                "category": self.category.pk,
                "level": "1-debutant",
                "brief": brief,
                "mode": mode,
            },
        )


class CourseAuthoringTests(StudioTestCase):
    def test_learners_cannot_enter_the_studio(self):
        self.client.force_login(user("lea"))
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 403)

    def test_write_a_course_then_submit_and_publish(self):
        response = self.create()
        course = Course.objects.get()
        self.assertRedirects(response, reverse("courses:review_course", args=[course.pk]))
        self.assertEqual(
            (course.status, course.author, course.kind),
            (Course.Status.WRITING, self.author, "theme"),
        )
        self.assertContains(self.client.get(reverse("studio:home")), "Le pare-feu expliqué")
        action = reverse("courses:review_course_action", args=[course.pk])
        for _ in range(2):
            self.client.post(action, {"action": "ajouter-lecon"})
        lessons = list(course.lessons.all())
        self.assertEqual([l.position for l in lessons], [1, 2])
        self.client.post(
            reverse("courses:review_lesson", args=[lessons[1].pk]), {"action": "supprimer"}
        )
        lesson = course.lessons.get()
        self.client.post(
            reverse("courses:review_lesson", args=[lesson.pk]),
            {
                "title": "Le filtrage",
                "body": LONG_BODY,
                "objective": "Comprendre",
                "key_points": "A\nB",
            },
        )
        add = reverse("courses:add_question", args=[course.pk])
        for n in range(3):
            self.client.post(
                add,
                {
                    "lecon": "1",
                    "text": f"Que fait un pare-feu ? ({n})",
                    "correct": "Il filtre",
                    "wrong_1": f"Il chiffre {n}",
                    "wrong_2": f"Il sauvegarde {n}",
                },
            )
        self.client.post(
            add, {"lecon": "1", "text": "Trop court ?", "correct": "A", "wrong_1": "A"}
        )  # refusée
        self.assertEqual(lesson.questions.count(), 3)
        self.client.post(action, {"action": "soumettre"})
        self.assertEqual(Course.objects.get().status, Course.Status.WRITING)  # pas encore d'examen
        for n in range(5):
            self.client.post(
                add,
                {
                    "lecon": "examen",
                    "text": f"Question d'examen numéro {n}",
                    "correct": "Oui",
                    "wrong_1": f"Non {n}",
                    "wrong_2": f"Peut-être {n}",
                },
            )
        self.client.post(action, {"action": "soumettre"})
        course.refresh_from_db()
        self.assertEqual(course.status, Course.Status.REVIEW)
        self.client.post(
            reverse("courses:review_lesson", args=[lesson.pk]),
            {"title": "Autre", "body": LONG_BODY},
        )
        self.assertEqual(
            Lesson.objects.get().title, "Le filtrage"
        )  # soumis : plus modifiable par le rédacteur
        self.client.force_login(self.manager)
        self.assertContains(
            self.client.get(reverse("catalogue:review_list"), {"type": "cours"}), "Écrit par redac"
        )
        self.client.post(action, {"action": "renvoyer"})
        self.assertEqual(Course.objects.get().status, Course.Status.WRITING)
        self.client.post(action, {"action": "publier"})
        self.assertEqual(Course.objects.get().status, Course.Status.PUBLISHED)

    def test_other_authors_cannot_open_a_draft(self):
        self.create()
        course = Course.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("courses:review_course", args=[course.pk])).status_code, 403
        )
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse("courses:review_course", args=[course.pk])).status_code, 200
        )
        self.assertContains(
            self.client.get(reverse("studio:home")), "par redac"
        )  # brouillons de tous

    def test_first_draft_by_the_ai_comes_back_to_the_author(self):
        self.assertContains(self.create(mode="ia", brief="court"), "décrivez en quelques phrases")
        self.create(mode="ia")
        course = Course.objects.get()
        self.assertEqual(course.status, Course.Status.PENDING)
        prompts = []

        def chat(messages, schema, temperature=0.5):
            prompts.append(messages[-1]["content"])
            return course_chat(messages, schema, temperature)
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(coursegen.quizgen, "chat", side_effect=chat):
            coursegen.generate(coursegen.take_next())
        course.refresh_from_db()
        self.assertEqual(
            (course.status, course.title), (Course.Status.WRITING, "Le pare-feu expliqué")
        )
        self.assertIn("Parler du filtrage des flux", prompts[0])
        self.assertEqual(
            list(coursegen.themes_without_course()), [self.category]
        )  # le thème reste à générer


class PodcastAuthoringTests(StudioTestCase):
    def test_write_a_series(self):
        self.create(kind="podcast")
        series = Series.objects.get()
        self.assertEqual((series.status, series.author), (Series.Status.WRITING, self.author))
        action = reverse("podcasts:review_series_action", args=[series.pk])
        self.client.post(action, {"action": "ajouter-episode"})
        episode = series.episodes.get()
        self.client.post(
            reverse("podcasts:review_episode", args=[episode.pk]),
            {"title": "Le filtrage", "body": "Clara : Bonjour !\nHugo : Bonjour Clara."},
        )
        episode.refresh_from_db()
        self.assertEqual(len(episode.script), 2)  # brouillon incomplet accepté en rédaction
        self.client.post(action, {"action": "soumettre"})
        self.assertEqual(
            Series.objects.get().status, Series.Status.WRITING
        )  # trop court pour la relecture
        script = "\n".join(
            f"{'Clara' if n % 2 == 0 else 'Hugo'} : réplique {n}. " + "mot " * 50 for n in range(12)
        )
        self.client.post(
            reverse("podcasts:review_episode", args=[episode.pk]),
            {"title": "Le filtrage", "body": script},
        )
        self.client.post(action, {"action": "soumettre"})
        self.assertEqual(Series.objects.get().status, Series.Status.REVIEW)
        self.client.force_login(self.manager)
        self.client.post(action, {"action": "valider"})
        self.assertEqual(Series.objects.get().status, Series.Status.TO_RECORD)

    def test_authored_series_do_not_take_the_theme(self):
        self.create(kind="podcast")
        self.assertEqual(list(podcastgen.themes_without_series()), [self.category])

    def test_fidelity_check_flags_lines(self):
        self.create(kind="podcast")
        series = Series.objects.get()
        episode = Episode.objects.create(
            series=series,
            position=1,
            title="Épisode",
            script=[{"speaker": 0, "text": "Bonjour."}, {"speaker": 1, "text": "Faux."}],
        )
        with mock.patch.object(ai, "context_for", return_value="extraits"), mock.patch.object(
            podcastgen, "check_script", return_value=[{"index": 1, "reason": "Inventé."}]
        ):
            self.client.post(reverse("studio:ai_check_episode", args=[episode.pk]))
        episode.refresh_from_db()
        self.assertEqual(episode.flags, [{"index": 1, "reason": "Inventé."}])


class AIHelpTests(StudioTestCase):
    def post(self, **payload):
        return self.client.post(
            reverse("studio:ai_stream"), json.dumps(payload), content_type="application/json"
        )

    def test_streamed_help(self):
        with mock.patch.object(
            ai, "context_for", return_value="Le pare-feu filtre les flux."
        ) as context, mock.patch.object(
            assistant, "open_stream", return_value=iter(["Un ", "brouillon."])
        ) as stream:
            response = self.post(
                action="rediger",
                kind="cours",
                title="Le filtrage",
                objective="Comprendre",
                brief="Pour débutants",
                category="reseau",
            )
            self.assertEqual(b"".join(response.streaming_content).decode(), "Un brouillon.")
        context.assert_called_once()
        messages = stream.call_args.args[0]
        self.assertIn("## Titre", messages[0]["content"])
        self.assertIn("Le pare-feu filtre les flux.", messages[1]["content"])
        self.assertIn("Consignes du rédacteur : Pour débutants", messages[1]["content"])

    def test_refusals(self):
        self.assertEqual(
            self.post(action="simplifier", kind="cours", text="court").status_code, 400
        )
        self.assertEqual(self.post(action="inconnue", kind="cours", text="x" * 50).status_code, 400)
        generation.voice_busy()
        response = self.post(action="corriger", kind="cours", text="Un passage à corriger. " * 3)
        self.assertEqual(response.status_code, 503)
        self.assertIn("enregistrement d'un audio", response.json()["error"])
        self.client.force_login(user("lea"))
        self.assertEqual(self.post(action="corriger", kind="cours", text="x" * 50).status_code, 403)

    def test_dialogue_and_selection_prompts(self):
        messages = ai.build_messages(
            "dialogue", "podcast", text="Le pare-feu filtre les flux réseau."
        )
        self.assertIn("Clara : ", messages[0]["content"])
        self.assertIn("dialogue", messages[1]["content"])
        check = ai.build_messages("verifier", "cours", text="Un texte à vérifier " * 3, category="")
        self.assertIn("Rien à signaler", check[1]["content"])

    def test_questions_by_the_ai(self):
        self.create()
        course = Course.objects.get()
        lesson = Lesson.objects.create(
            course=course, position=1, title="Le filtrage", body=LONG_BODY
        )
        found = [
            {
                "text": f"Question {n} sur le filtrage ?",
                "choices": ["Oui", "Non", "Peut-être"],
                "explanation": "Parce que.",
            }
            for n in range(4)
        ]
        with mock.patch.object(coursegen, "more_questions", return_value=found):
            self.client.post(reverse("studio:ai_questions", args=[course.pk]), {"lecon": "1"})
        self.assertEqual(lesson.questions.count(), 4)
        self.assertEqual(CourseQuestion.objects.filter(lesson=lesson).first().answer, 0)
