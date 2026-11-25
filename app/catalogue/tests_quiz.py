"""Tests de connaissances : règles d'accès, correction et import des fichiers de questions."""
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import quiz, reading
from .management.commands import import_quizzes
from .models import Guide, QuizAttempt, QuizQuestion

User = get_user_model()
PASSWORD = "Un-mot-de-passe-solide-42!"


def make_questions(guide, series=6, per_series=5):
    for number in range(1, series + 1):
        for position in range(per_series):
            QuizQuestion.objects.create(
                guide=guide,
                series=number,
                position=position,
                text=f"Q{number}.{position}",
                choices=["A", "B", "C", "D"],
                answer=1,
            )


class QuizAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password=PASSWORD)
        self.client.force_login(self.user)
        self.guide = Guide.objects.create(title="Guide TLS", slug="tls")
        make_questions(self.guide)

    def url(self, number):
        return reverse("catalogue:quiz_take", args=["tls", number])

    def answer_all(self, number, choice="1"):
        questions = QuizQuestion.objects.filter(guide=self.guide, series=number)
        return self.client.post(self.url(number), {f"q{q.pk}": choice for q in questions})

    def states(self):
        return [s.state for s in quiz.series_status(self.user, self.guide)]

    def test_guide_must_be_read_first(self):
        self.assertEqual(self.states(), [quiz.LOCKED] * 6)
        self.assertRedirects(self.client.get(self.url(1)), self.guide.get_absolute_url())

    def test_one_new_series_per_day(self):
        reading.mark_finished(self.user, self.guide)
        self.assertEqual(self.states()[:2], [quiz.AVAILABLE, quiz.LOCKED])
        self.answer_all(1)
        status = quiz.series_status(self.user, self.guide)
        self.assertEqual(status[0].state, quiz.DONE)
        self.assertEqual(status[1].unlock_on, timezone.localdate() + timedelta(days=1))
        # Pas d'avance : la série 2 reste fermée le jour même.
        self.assertRedirects(self.client.get(self.url(2)), reverse("catalogue:tests_overview"))

        QuizAttempt.objects.update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.states()[:3], [quiz.DONE, quiz.AVAILABLE, quiz.LOCKED])

    def test_no_assistant_during_the_test_only(self):
        reading.mark_finished(self.user, self.guide)
        self.assertNotContains(self.client.get(self.url(1)), "Une question ?")
        self.assertContains(self.answer_all(1), "Une question ?")  # résultats : l'aide revient
        self.assertNotContains(self.client.get(reverse("catalogue:daily_review")), "Une question ?")

    def test_missed_days_do_not_unlock_several_series(self):
        reading.mark_finished(self.user, self.guide)
        self.answer_all(1)
        QuizAttempt.objects.update(created_at=timezone.now() - timedelta(days=10))
        self.assertEqual(self.states(), [quiz.DONE, quiz.AVAILABLE] + [quiz.LOCKED] * 4)

    def test_done_series_can_be_retaken_and_best_score_is_kept(self):
        reading.mark_finished(self.user, self.guide)
        self.answer_all(1, choice="1")  # 5/5
        response = self.answer_all(1, choice="0")  # 0/5
        self.assertEqual(response.context["attempt"].score, 0)
        self.assertEqual(QuizAttempt.objects.count(), 2)
        self.assertEqual(quiz.series_status(self.user, self.guide)[0].best, 100)

    def test_grading_ignores_invalid_answers(self):
        questions = list(QuizQuestion.objects.filter(guide=self.guide, series=1))
        score, answers = quiz.grade(
            questions,
            {f"q{questions[0].pk}": "1", f"q{questions[1].pk}": "9", f"q{questions[2].pk}": "x"},
        )
        self.assertEqual(score, 1)
        self.assertEqual(answers[str(questions[1].pk)], None)
        self.assertEqual(answers[str(questions[2].pk)], None)

    def test_overview_lists_todays_series(self):
        reading.mark_finished(self.user, self.guide)
        response = self.client.get(reverse("catalogue:tests_overview"))
        self.assertEqual([e["guide"] for e in response.context["today"]], [self.guide])
        self.assertContains(response, self.url(1))

    def test_catalogue_shows_which_guides_have_a_quiz(self):
        Guide.objects.create(title="Guide sans test", slug="sans-test")
        response = self.client.get(reverse("catalogue:guide_list"))
        counts = {g.slug: g.quiz_series for g in response.context["guides"]}
        self.assertEqual(counts, {"tls": 6, "sans-test": 0})
        self.assertContains(response, "QCM · 6 séries", count=1)

        reading.add(self.user, self.guide)
        self.assertContains(self.client.get(reverse("catalogue:reading_list")), "QCM · 6 séries")
        self.assertContains(self.client.get(reverse("catalogue:guide_detail", args=["sans-test"])),
                            "Aucun questionnaire")

    def test_guides_can_be_filtered_with_or_without_quiz(self):
        Guide.objects.create(title="Guide sans test", slug="sans-test")
        url = reverse("catalogue:guide_list")
        for value, expected in (
            ("avec", ["tls"]),
            ("sans", ["sans-test"]),
            ("", ["sans-test", "tls"]),
            ("autre", ["sans-test", "tls"]),
        ):
            response = self.client.get(url, {"qcm": value})
            self.assertEqual(sorted(g.slug for g in response.context["guides"]), expected, value)
        # Le filtre est conservé en passant aux autres onglets.
        response = self.client.get(url, {"qcm": "avec"})
        self.assertContains(response, reverse("catalogue:referential_list") + "?qcm=avec")


class QuizImportTests(TestCase):
    def setUp(self):
        self.guide = Guide.objects.create(title="Guide SSH", slug="ssh", anssi_id="usage-ssh")

    def run_import(self, questions):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "usage-ssh.json"
            path.write_text(
                json.dumps({"guide": "usage-ssh", "questions": questions}), encoding="utf-8"
            )
            with mock.patch.object(import_quizzes, "DATA_DIR", Path(folder)):
                call_command("import_quizzes", stdout=mock.MagicMock())

    def questions(self, per_series=5):
        return [
            {
                "series": s,
                "question": f"Question {s}-{i} ?",
                "choices": ["bonne", "b", "c", "d"],
                "explanation": "Parce que.",
                "source": "R1",
            }
            for s in range(1, 7)
            for i in range(per_series)
        ]

    def test_first_choice_is_the_answer_and_choices_are_shuffled(self):
        self.run_import(self.questions())
        imported = list(QuizQuestion.objects.filter(guide=self.guide))
        self.assertEqual(len(imported), 30)
        self.assertTrue(all(q.choices[q.answer] == "bonne" for q in imported))
        self.assertGreater(len({q.answer for q in imported}), 1)  # pas toujours à la même place

    def test_incomplete_series_are_rejected(self):
        with self.assertRaises(CommandError):
            self.run_import(self.questions(per_series=4))

    def test_reimport_keeps_question_ids(self):
        """Les identifiants restent stables : l'historique des réponses reste valable."""
        self.run_import(self.questions(per_series=6))
        before = set(QuizQuestion.objects.values_list("pk", flat=True))
        edited = self.questions(per_series=5)
        edited[0]["question"] = "Question corrigée ?"
        self.run_import(edited)
        after = dict(QuizQuestion.objects.values_list("pk", "text"))
        self.assertEqual(len(after), 30)  # la 6e question de chaque série est supprimée
        self.assertTrue(set(after) < set(before))
        self.assertIn("Question corrigée ?", after.values())
