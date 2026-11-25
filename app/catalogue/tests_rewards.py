"""Barème d'XP des activités du catalogue."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from progression import xp
from progression.models import XPEvent

from . import reading, rewards
from .models import DailyReview, Guide, QuizAttempt, QuizQuestion
from .tests_quiz import make_questions

User = get_user_model()


class RewardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)
        self.guide = Guide.objects.create(title="Guide TLS", slug="tls", estimated_minutes=45)
        make_questions(self.guide)

    def answers(self, series, right):
        questions = QuizQuestion.objects.filter(guide=self.guide, series=series)
        return {
            f"q{q.pk}": str(q.answer if i < right else (q.answer + 1) % 4)
            for i, q in enumerate(questions)
        }

    def test_finishing_a_reading(self):
        response = self.client.post(
            reverse("catalogue:reading_finish"), {"kind": "guide", "slug": "tls"}, follow=True
        )
        self.assertContains(response, "+25 XP")  # 10 + 45 // 3
        self.client.post(reverse("catalogue:reading_finish"), {"kind": "guide", "slug": "tls"})
        self.assertEqual(xp.total_xp(self.user), 25)
        long_guide = Guide(title="Long", estimated_minutes=600)
        self.assertEqual(rewards.reading_xp(long_guide), rewards.READING_MAX)

    def test_series_rewards_only_new_correct_answers(self):
        reading.mark_finished(self.user, self.guide)
        url = reverse("catalogue:quiz_take", args=["tls", 1])
        first = self.client.post(url, self.answers(1, right=3)).context["gain"]
        self.assertEqual(first.xp, 10 + 3 * 2)
        second = self.client.post(url, self.answers(1, right=5)).context["gain"]
        self.assertEqual(second.xp, 2 * 2 + 5)  # deux nouvelles bonnes réponses + sans faute
        third = self.client.post(url, self.answers(1, right=5))
        self.assertEqual(third.context["gain"].xp, 0)
        self.assertContains(third, "Déjà obtenus")

    def test_daily_review_rewards(self):
        reading.mark_finished(self.user, self.guide)
        self.client.post(reverse("catalogue:quiz_take", args=["tls", 1]), self.answers(1, right=5))
        before = xp.total_xp(self.user)
        review = DailyReview.objects.create(
            user=self.user,
            date=reading.timezone.localdate(),
            question_ids=list(
                QuizQuestion.objects.filter(guide=self.guide, series=1).values_list("pk", flat=True)
            ),
        )
        response = self.client.post(
            reverse("catalogue:daily_review"),
            {f"q{pk}": str(QuizQuestion.objects.get(pk=pk).answer) for pk in review.question_ids},
        )
        self.assertEqual(response.context["gain"].xp, 5 + 5 * 2 + 5)
        self.assertEqual(xp.total_xp(self.user), before + 20)

    def test_backfill_is_idempotent(self):
        reading.mark_finished(self.user, self.guide)
        questions = list(QuizQuestion.objects.filter(guide=self.guide, series=1))
        QuizAttempt.objects.create(user=self.user, guide=self.guide, series=1, score=5, total=5,
                                   answers={str(q.pk): q.answer for q in questions})
        self.assertEqual(rewards.backfill(self.user), 25 + 10 + 10 + 5)
        self.assertEqual(rewards.backfill(self.user), 0)
        self.assertEqual(XPEvent.objects.filter(user=self.user, kind="test").count(), 7)

    def test_emeralds_for_perfect_series_and_finished_guide(self):
        from progression.models import EmeraldEvent
        reading.mark_finished(self.user, self.guide)
        for series in range(1, 7):
            questions = list(QuizQuestion.objects.filter(guide=self.guide, series=series))
            answers = {str(q.pk): q.answer for q in questions}
            QuizAttempt.objects.create(
                user=self.user, guide=self.guide, series=series, score=5, total=5, answers=answers
            )  # comme la vue, avant la récompense
            rewards.reward_series(self.user, self.guide, series, questions, answers)
        refs = set(EmeraldEvent.objects.filter(user=self.user).values_list("ref", flat=True))
        self.assertTrue({f"parfait:serie:{self.guide.pk}:{n}" for n in range(1, 7)} <= refs)
        self.assertIn(f"guide:{self.guide.pk}", refs)
        self.assertEqual(EmeraldEvent.objects.filter(user=self.user, kind="complet").count(), 1)
