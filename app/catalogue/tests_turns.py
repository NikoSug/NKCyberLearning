"""Ordre de passage : une seule génération à la fois, la première de l'ordre qui a du travail."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CONTENT_MANAGERS_GROUP

from . import generation, narration
from .models import GenerationControl, Glossary, Guide, Narration, QuizDraft

Kind = GenerationControl.Kind


class TurnTests(TestCase):
    def setUp(self):
        for kind in generation.BACKGROUND:  # tous les services tournent
            generation.heartbeat(kind)
        self.guides = [Guide.objects.create(title=f"Guide {n}", slug=f"g{n}") for n in range(3)]

    def test_default_order(self):
        self.assertEqual(
            generation.order(), [Kind.GLOSSARY, Kind.QUIZ, Kind.COURSE, Kind.PODCAST, Kind.AUDIO]
        )

    def test_first_kind_with_work_has_the_hand(self):
        QuizDraft.objects.create(guide=self.guides[0])
        Narration.objects.create(guide=self.guides[1], priority=narration.BULK_PRIORITY)
        self.assertIsNone(generation.holder(Kind.QUIZ))
        self.assertEqual(generation.holder(Kind.AUDIO), Kind.QUIZ)
        self.assertIsNone(
            generation.holder(Kind.GLOSSARY)
        )  # avant les QCM dans l'ordre (rien à faire, mais libre)
        Glossary.objects.create(guide=self.guides[2])
        self.assertEqual(generation.holder(Kind.QUIZ), Kind.GLOSSARY)

    def test_running_item_is_never_interrupted(self):
        Narration.objects.create(guide=self.guides[1], status=Narration.Status.PROCESSING)
        Glossary.objects.create(guide=self.guides[2])
        self.assertEqual(
            generation.holder(Kind.GLOSSARY), Kind.AUDIO
        )  # l'audio termine son guide d'abord

    def test_paused_or_stopped_services_let_the_others_pass(self):
        Glossary.objects.create(guide=self.guides[2])
        QuizDraft.objects.create(guide=self.guides[0])
        generation.set_paused(Kind.GLOSSARY, True)
        self.assertIsNone(generation.holder(Kind.QUIZ))
        generation.set_paused(Kind.GLOSSARY, False)
        GenerationControl.objects.filter(kind=Kind.GLOSSARY).update(
            last_seen=timezone.now() - generation.ALIVE_AFTER * 2
        )
        self.assertIsNone(generation.holder(Kind.QUIZ))  # service arrêté : ne bloque personne

    def test_user_audio_request_goes_first(self):
        QuizDraft.objects.create(guide=self.guides[0])
        Narration.objects.create(guide=self.guides[1], priority=narration.USER_PRIORITY)
        self.assertIsNone(generation.holder(Kind.AUDIO))
        self.assertEqual(generation.holder(Kind.QUIZ), Kind.AUDIO)

    def test_user_audio_priority_can_be_turned_off(self):
        QuizDraft.objects.create(guide=self.guides[0])
        Narration.objects.create(guide=self.guides[1], priority=narration.USER_PRIORITY)
        generation.set_audio_users_first(False)
        self.assertEqual(
            generation.holder(Kind.AUDIO), Kind.QUIZ
        )  # l'audio suit l'ordre de passage
        manager = get_user_model().objects.create_user(
            "gestion", password="Un-mot-de-passe-solide-42!"
        )
        manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.client.force_login(manager)
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")), "suivent l'ordre de passage"
        )
        self.client.post(
            reverse("catalogue:generation_action", args=["ordre"]), {"action": "utilisateurs-oui"}
        )
        self.assertIsNone(generation.holder(Kind.AUDIO))

    def test_order_can_be_changed(self):
        generation.move_in_order(Kind.AUDIO, -1)
        generation.move_in_order(Kind.GLOSSARY, 1)
        self.assertEqual(
            generation.order(), [Kind.QUIZ, Kind.GLOSSARY, Kind.COURSE, Kind.AUDIO, Kind.PODCAST]
        )
        generation.move_in_order(Kind.QUIZ, -1)  # déjà premier : rien ne change
        self.assertEqual(generation.order()[0], Kind.QUIZ)

    def test_dashboard_shows_order_and_waiting_state(self):
        manager = get_user_model().objects.create_user(
            "gestion", password="Un-mot-de-passe-solide-42!"
        )
        manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.client.force_login(manager)
        QuizDraft.objects.create(guide=self.guides[0])
        Narration.objects.create(guide=self.guides[1], priority=narration.BULK_PRIORITY)
        page = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(page, "Ordre de passage")
        self.assertContains(page, "Attend son tour : 🧠 QCM passe avant")
        names = [item["kind"] for item in page.context["services"]]
        self.assertEqual(names, ["lexique", "qcm", "cours", "podcast", "audio"])
        response = self.client.post(reverse("catalogue:generation_action", args=["ordre"]),
                                    {"action": "monter", "item": "audio"})
        self.assertRedirects(response, reverse("catalogue:generation_dashboard") + "#ordre",
                             fetch_redirect_response=False)
        self.assertEqual(generation.order()[3], Kind.AUDIO)
        self.client.post(
            reverse("catalogue:generation_action", args=["ordre"]),
            {"action": "monter", "item": "inconnu"},
        )
        self.assertEqual(generation.order()[3], Kind.AUDIO)
