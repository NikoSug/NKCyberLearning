"""Révision du jour : questions éligibles, priorités de la répétition espacée, mélange des
guides."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import reading, revision
from .models import DailyReview, DailyReviewAttempt, Guide, QuizAttempt, QuizQuestion
from .tests_quiz import PASSWORD, make_questions

User = get_user_model()


class ReviewHelpers:
    def setUp(self):
        self.user = User.objects.create_user("alice", password=PASSWORD)
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def read_guide(self, slug):
        guide = Guide.objects.create(title=f"Guide {slug}", slug=slug)
        make_questions(guide)
        reading.mark_finished(self.user, guide)
        return guide

    def answer(self, guide, series, wrong=(), days_ago=1):
        """Enregistre une tentative de série ; les questions de `wrong` sont ratées."""
        questions = QuizQuestion.objects.filter(guide=guide, series=series)
        answers = {str(q.pk): (q.answer + 1) % 4 if q in wrong else q.answer for q in questions}
        return QuizAttempt.objects.create(
            user=self.user, guide=guide, series=series, total=len(answers),
            score=sum(1 for q in questions if q not in wrong), answers=answers,
            created_at=timezone.now() - timedelta(days=days_ago))


class DailyReviewTests(ReviewHelpers, TestCase):
    def test_no_review_before_a_first_series(self):
        self.read_guide("tls")
        self.assertIsNone(revision.todays_review(self.user))
        response = self.client.get(reverse("catalogue:tests_overview"))
        self.assertNotContains(response, "Révision du jour")
        self.assertRedirects(self.client.get(reverse("catalogue:daily_review")),
                             reverse("catalogue:tests_overview"))

    def test_only_series_already_done_are_used(self):
        guide = self.read_guide("tls")
        self.answer(guide, 1)
        chosen = revision.select_questions(self.user, self.today)
        self.assertEqual(len(chosen), 5)
        self.assertEqual({q.series for q in chosen}, {1})  # pas de questions des séries fermées

    def test_unread_guides_are_ignored(self):
        guide = self.read_guide("tls")
        self.answer(guide, 1)
        reading.mark_unfinished(reading.get_item(self.user, guide))
        self.assertEqual(revision.select_questions(self.user, self.today), [])

    def test_questions_from_several_guides_are_mixed(self):
        guides = [self.read_guide(slug) for slug in ("tls", "ssh", "ipsec")]
        for guide in guides:
            for series in (1, 2):
                self.answer(guide, series)
        chosen = revision.select_questions(self.user, self.today)
        self.assertEqual(len(chosen), revision.REVIEW_SIZE)
        per_guide = [sum(q.guide_id == g.pk for q in chosen) for g in guides]
        self.assertTrue(all(2 <= n <= 4 for n in per_guide), per_guide)

    def test_missed_questions_come_back_first(self):
        guide = self.read_guide("tls")
        for series in (1, 2, 3):
            self.answer(guide, series, days_ago=10)
        missed = list(QuizQuestion.objects.filter(guide=guide, series=2)[:3])
        self.answer(guide, 2, wrong=missed, days_ago=1)
        chosen = revision.select_questions(self.user, self.today, size=3)
        self.assertEqual(set(chosen), set(missed))

    def test_spaced_repetition_intervals(self):
        entry = revision.History(
            streak=1, last_seen=self.today - timedelta(days=1), last_correct=True
        )
        self.assertEqual(
            revision.priority(entry, self.today)[0], revision.LATER
        )  # revient dans 2 jours
        entry.last_seen = self.today - timedelta(days=2)
        self.assertEqual(revision.priority(entry, self.today)[0], revision.DUE)
        entry = revision.History(
            streak=3, last_seen=self.today - timedelta(days=7), last_correct=True
        )
        self.assertEqual(revision.priority(entry, self.today), (revision.LATER, 1))  # 8 jours
        entry = revision.History(
            streak=0, last_seen=self.today - timedelta(days=1), last_correct=False
        )
        self.assertEqual(revision.priority(entry, self.today)[0], revision.WRONG)
        entry.last_seen = self.today
        self.assertEqual(revision.priority(entry, self.today)[0], revision.SEEN_TODAY)
        self.assertEqual(revision.History(streak=10).interval(), revision.MAX_INTERVAL)

    def test_daily_selection_is_frozen_and_can_be_answered(self):
        guide = self.read_guide("tls")
        for series in (1, 2, 3):
            self.answer(guide, series)
        url = reverse("catalogue:daily_review")
        first = self.client.get(url).context["questions"]
        self.assertEqual(self.client.get(url).context["questions"], first)
        self.assertEqual(DailyReview.objects.count(), 1)

        response = self.client.post(url, {f"q{q.pk}": str(q.answer) for q in first})
        self.assertEqual(response.context["attempt"].score, len(first))
        self.assertEqual(DailyReviewAttempt.objects.count(), 1)
        overview = self.client.get(reverse("catalogue:tests_overview"))
        self.assertContains(overview, f"meilleur score {len(first)} / {len(first)}")

    def test_review_answers_feed_the_history(self):
        guide = self.read_guide("tls")
        self.answer(guide, 1, days_ago=5)
        question = QuizQuestion.objects.filter(guide=guide, series=1).first()
        review = DailyReview.objects.create(user=self.user, date=self.today - timedelta(days=1),
                                            question_ids=[question.pk])
        DailyReviewAttempt.objects.create(review=review, score=0, total=1,
                                          answers={str(question.pk): (question.answer + 1) % 4},
                                          created_at=timezone.now() - timedelta(days=1))
        history = revision.answer_history(self.user, [question])[question.pk]
        self.assertFalse(history.last_correct)
        self.assertEqual(history.streak, 0)
        self.assertEqual(revision.select_questions(self.user, self.today, size=1), [question])

    def test_streak_counts_consecutive_days(self):
        for days_ago in (0, 1, 2, 4):
            review = DailyReview.objects.create(
                user=self.user, date=self.today - timedelta(days=days_ago)
            )
            DailyReviewAttempt.objects.create(review=review, score=1, total=1)
        self.assertEqual(revision.streak_days(self.user), 3)


class SecondReviewTests(ReviewHelpers, TestCase):
    def setUp(self):
        super().setUp()
        from progression import emeralds
        from progression.models import EmeraldEvent, XPEvent
        from progression import xp as progression
        progression.grant(
            self.user,
            [progression.Award("niveau", progression.level_start(3), XPEvent.Kind.BONUS, "x")],
            when=timezone.now() - timedelta(days=30),
        )
        emeralds.credit(self.user, "cadeau", 100, EmeraldEvent.Kind.LEVEL, "Cadeau")
        self.guide = self.read_guide("tls")
        for series in (1, 2, 3):
            self.answer(self.guide, series)

    def test_second_review_requires_the_first_one(self):
        self.assertRedirects(
            self.client.post(reverse("catalogue:buy_second_review")),
            reverse("catalogue:daily_review"),
        )
        self.assertRedirects(self.client.get(reverse("catalogue:daily_review_number", args=[2])),
                             reverse("catalogue:daily_review"))

    def test_second_review_has_new_questions(self):
        url = reverse("catalogue:daily_review")
        first = self.client.get(url).context["questions"]
        response = self.client.post(url, {f"q{q.pk}": str(q.answer) for q in first})
        self.assertTrue(response.context["second_possible"])
        response = self.client.post(reverse("catalogue:buy_second_review"))
        self.assertRedirects(response, reverse("catalogue:daily_review_number", args=[2]))
        second = self.client.get(reverse("catalogue:daily_review_number", args=[2])).context[
            "questions"
        ]
        self.assertTrue(second)
        self.assertFalse(set(second) & set(first))
        gain = self.client.post(reverse("catalogue:daily_review_number", args=[2]),
                                {f"q{q.pk}": str(q.answer) for q in second}).context["gain"]
        self.assertGreater(gain.xp, 0)  # récompensée comme une révision à part entière
        self.assertEqual(
            self.client.post(reverse("catalogue:buy_second_review")).status_code, 302
        )  # une par jour
        from progression.models import EmeraldEvent
        self.assertEqual(
            EmeraldEvent.objects.filter(ref__startswith="achat:seconde-revision").count(), 1
        )
