"""Génération des QCM par IA locale (modèle simulé) et module de relecture."""
import random
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CONTENT_MANAGERS_GROUP

from . import generation, narration_text, quizgen
from .management.commands import import_quizzes
from .models import Guide, GuideDocument, QuizDraft, QuizDraftQuestion, QuizQuestion, Referential

User = get_user_model()
Status = QuizDraft.Status
Decision = QuizDraftQuestion.Decision
SECTIONS = [
    narration_text.Section(f"Chapitre {n}", f"Texte du chapitre {n}. " * 40) for n in range(1, 9)
]


SUBJECTS = [
    "pare-feu",
    "journal",
    "certificat",
    "sauvegarde",
    "annuaire",
    "sonde",
    "routeur",
    "poste",
    "serveur",
    "messagerie",
    "proxy",
    "badge",
    "tunnel",
    "clé",
    "hyperviseur",
    "conteneur",
    "imprimante",
    "téléphone",
    "navigateur",
    "commutateur",
]
VERBS = [
    "configurer",
    "surveiller",
    "protéger",
    "isoler",
    "vérifier",
    "renouveler",
    "documenter",
    "mettre à jour",
    "désactiver",
    "chiffrer",
]
COUNTER = iter(range(10 ** 6))


def fake_questions(prefix, count=6):
    questions = []
    for _ in range(count):
        n = next(COUNTER)
        words = random.Random(n).sample(SUBJECTS + VERBS, 7)  # nosec B311 (données de test)
        questions.append(
            {
                "question": " ".join(words).capitalize() + " ?",
                "bonne_reponse": f"Bonne réponse {prefix} {n}",
                "mauvaises_reponses": [
                    f"Leurre A {prefix} {n} (non mentionné dans le texte)",
                    f"Leurre B {prefix} {n}",
                    f"Leurre C {prefix} {n}.",
                ],
                "explication": "Parce que le texte le dit.",
                "source": f"Section 3, recommandation R{n}",
            }
        )
    return {"questions": questions}


def fake_chat(messages, schema, temperature=0.4):
    """Simule le modèle : questions pour la génération, bonnes lettres pour la vérification
    croisée."""
    if "reponses" in schema["properties"]:
        prompt = messages[-1]["content"]
        letters = []
        for line in prompt.splitlines():
            if line[:1].isdigit():
                letter = next(c for c in "ABCDE" if f"{c}) Bonne réponse" in line)
                letters.append(letter)
        return {"reponses": letters}
    level = next(n for n in quizgen.SERIES if f"niveau {n} sur 6" in messages[-1]["content"])
    return fake_questions(f"S{level}")


class CheckTests(TestCase):
    def test_bad_questions_are_refused(self):
        good = {
            "question": "Quelle est la taille minimale d'une clé RSA ?",
            "bonne_reponse": "2048 bits",
            "mauvaises_reponses": ["1024 bits", "512 bits", "768 bits"],
            "explication": "R8.",
            "source": "R8",
        }
        self.assertIsNotNone(quizgen.check_question(good, []))
        for change in (
            {"mauvaises_reponses": ["1024 bits", "1024 bits", "768 bits"]},  # doublon
            {"mauvaises_reponses": ["Toutes les réponses", "512 bits", "768 bits"]},  # interdit
            {"mauvaises_reponses": ["512 bits"]},  # trop peu
            {"question": "Clé ?"},
        ):  # trop court
            self.assertIsNone(quizgen.check_question({**good, **change}, []), change)
        self.assertIsNone(
            quizgen.check_question(good, ["Quelle est la taille minimale d'une clé RSA ?"])
        )

    def test_long_texts_get_more_questions_and_situations_ask_for_a_diagram(self):
        sizes = [
            quizgen.per_series(quizgen.Part("P", "x" * n)) for n in (2600, 12000, 14000, 40000)
        ]
        self.assertEqual(
            sizes, [5, 6, 7, 7]
        )  # guide court : 5 ; référentiel PASSI (~14 000 car. par série) : 7
        prompts = []
        def chat(messages, schema, temperature=0.4):
            prompts.append(messages[-1]["content"])
            return {"questions": []}
        with mock.patch.object(quizgen, "chat", side_effect=chat):
            quizgen.generate_series(quizgen.Part("P", "Texte."), 2, 9, [])
            self.assertNotIn("schéma", prompts[-1])
            quizgen.generate_series(quizgen.Part("P", "Texte."), 4, 9, [])
        self.assertIn(quizgen.DIAGRAM_REQUEST, prompts[-1])
        self.assertIn("Rédige 6 questions", prompts[-1])  # 9 attendues, demandées par lots

    def test_series_prefers_recommendations_not_yet_asked(self):
        def item(n, source):
            return {
                "question": f"{' '.join(random.Random(n).sample(SUBJECTS + VERBS, 7))} ?",
                "bonne_reponse": f"Bonne {n}",
                "mauvaises_reponses": [f"Leurre {n}{x}" for x in "ABC"],
                "explication": "…",
                "source": source,
            }
        part = quizgen.Part("Partie", "Texte.")
        answers = [
            {"questions": [item(1, "R31"), item(2, "R31"), item(3, "R32")]},
            {"questions": [item(4, "R33")]},
        ]
        with mock.patch.object(quizgen, "chat", side_effect=answers):
            found = quizgen.generate_series(part, 5, 3, [])
        self.assertEqual([q["source"] for q in found], ["R31", "R32", "R33"])
        # Faute d'autre recommandation dans la partie, la même est reprise plutôt que d'échouer
        with mock.patch.object(
            quizgen, "chat", return_value={"questions": [item(5, "R31"), item(6, "R31")]}
        ):
            found = quizgen.generate_series(part, 5, 2, [], sources=["R31"])
        self.assertEqual([q["source"] for q in found], ["R31", "R31"])

    def test_choices_and_sources_are_cleaned(self):
        self.assertEqual(
            quizgen.clean_choice("3DES en mode CBC (utilisé uniquement avant la version 5.3)"),
            "3DES en mode CBC",
        )
        self.assertEqual(quizgen.clean_choice("Mode CBC, considéré comme vulnérable."), "Mode CBC")
        self.assertEqual(quizgen.clean_choice("Clé ECDSA (256 bits)"), "Clé ECDSA (256 bits)")
        self.assertEqual(quizgen.short_source("Section 3, recommandation R 12"), "R12")
        self.assertEqual(quizgen.short_source("Partie 5 - X11Forwarding"), "X11Forwarding")

    def test_guide_is_split_into_six_parts_in_order(self):
        parts = quizgen.split_parts(SECTIONS)
        self.assertEqual(len(parts), 6)
        self.assertTrue(parts[0].text.startswith("## Chapitre 1"))
        self.assertIn("Chapitre 8", parts[-1].text)
        short = quizgen.split_parts(SECTIONS[:2])
        self.assertEqual(len(short), 6)  # guide très court : parties réutilisées
        self.assertTrue(
            all(
                len(p.text) <= quizgen.MAX_CONTEXT_CHARS
                for p in quizgen.split_parts(
                    [
                        narration_text.Section("Long", "Paragraphe assez long. " * 40 + "\n")
                        for _ in range(60)
                    ]
                )
            )
        )


class GenerationTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.guide = Guide.objects.create(
            title="Guide pare-feu", slug="pare-feu", anssi_id="pare-feu"
        )
        document = GuideDocument(guide=self.guide, label="PDF")
        document.file.save("pare-feu.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def run_generation(self):
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(quizgen, "chat", side_effect=fake_chat):
            return quizgen.generate(quizgen.take_next())

    def test_queue_only_takes_guides_without_quiz(self):
        self.assertEqual(list(quizgen.guides_without_quiz()), [self.guide])
        draft = quizgen.request(self.guide)
        self.assertEqual(quizgen.request(self.guide), draft)  # un seul brouillon actif
        self.assertEqual(list(quizgen.guides_without_quiz()), [])

    def test_generation_produces_six_checked_series(self):
        quizgen.request(self.guide)
        draft = self.run_generation()
        self.assertEqual(draft.status, Status.REVIEW)
        self.assertEqual(quizgen.accepted_by_series(draft), {n: 0 for n in quizgen.SERIES})
        questions = list(draft.questions.all())
        self.assertEqual(len(questions), 30)
        self.assertTrue(all(q.cross_check for q in questions))
        first = questions[0]
        self.assertTrue(first.choices[1].startswith("Leurre A S1"))
        self.assertNotIn("mentionné", first.choices[1])  # commentaire révélateur retiré
        self.assertFalse(first.choices[3].endswith("."))
        self.assertRegex(first.source, r"^R\d+$")

    def test_rejected_questions_are_regenerated_and_publication_exports(self):
        quizgen.request(self.guide)
        draft = self.run_generation()
        draft.questions.filter(series=2, position__lte=2).update(decision=Decision.REJECTED)
        draft.status = Status.PENDING
        draft.save()
        draft = self.run_generation()
        self.assertEqual(
            draft.questions.filter(series=2).exclude(decision=Decision.REJECTED).count(), 5
        )
        self.assertFalse(quizgen.can_publish(draft))
        draft.questions.exclude(decision=Decision.REJECTED).update(decision=Decision.ACCEPTED)
        self.assertTrue(quizgen.can_publish(draft))
        user = User.objects.create_user("relecteur", password="Un-mot-de-passe-solide-42!")
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            import_quizzes, "DATA_DIR", Path(folder)
        ):
            path = quizgen.publish(draft, user)
            self.assertTrue(path.exists())
        self.assertEqual(QuizQuestion.objects.filter(guide=self.guide).count(), 30)
        question = QuizQuestion.objects.filter(guide=self.guide).first()
        self.assertTrue(
            question.choices[question.answer].startswith("Bonne réponse")
        )  # mélangées, réponse suivie
        self.assertEqual(QuizDraft.objects.get(pk=draft.pk).status, Status.PUBLISHED)

    def test_anssi_referential_gets_its_own_quiz(self):
        referential = Referential.objects.create(
            name="Prestataires d'audit", short_name="PASSI", slug="passi"
        )
        referential.document.save("passi.pdf", ContentFile(b"%PDF-1.4 factice"), save=True)
        exam = Referential.objects.create(
            name="Programme CISSP", slug="cissp", kind=Referential.Kind.EXAM
        )
        with self.assertRaisesMessage(quizgen.GenerationError, "référentiels ANSSI"):
            quizgen.request(exam)  # pas de texte : l'IA inventerait les réponses
        quizgen.request(referential)
        # Le brouillon du référentiel (sans guide) ne bloque pas la file des guides (« NOT IN » avec
        # NULL)
        self.assertEqual(list(quizgen.guides_without_quiz()), [self.guide])
        self.client.force_login(
            User.objects.create_superuser("chef", password="Un-mot-de-passe-solide-42!")
        )
        self.assertContains(
            self.client.get(reverse("catalogue:generation_dashboard")), "Prestataires d&#x27;audit"
        )
        self.assertContains(
            self.client.get(reverse("catalogue:generation_queue", args=["qcm"])), "Prestataires d"
        )
        systems = []
        def chat(messages, schema, temperature=0.4):
            systems.append(messages[0]["content"])
            return fake_chat(messages, schema, temperature)
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(quizgen, "chat", side_effect=chat):
            draft = quizgen.generate(quizgen.take_next())
        self.assertIn("référentiel d'exigences", systems[0])
        self.assertContains(
            self.client.get(reverse("catalogue:review_detail", args=[draft.pk])),
            "Fiche du référentiel",
        )
        draft.questions.update(decision=Decision.ACCEPTED)
        self.assertIsNone(
            quizgen.publish(draft, None)
        )  # pas d'export JSON : il n'est réimporté que pour les guides
        self.assertEqual(QuizQuestion.objects.filter(referential=referential).count(), 30)
        self.assertFalse(QuizQuestion.objects.filter(guide=self.guide).exists())

    def test_guide_without_pdf_is_never_queued(self):
        # Un « guide » qui n'est qu'un modèle de tableur (liens .xlsx / .ods, aucun PDF) : rien à
        # interroger.
        sheet = Guide.objects.create(title="Outil de pilotage", slug="outil")
        GuideDocument.objects.create(
            guide=sheet, label="Modèle (XLSX)", source_url="https://exemple.fr/modele.xlsx"
        )
        self.assertNotIn(sheet, list(quizgen.guides_without_quiz()))
        with self.assertRaisesMessage(quizgen.GenerationError, "pas de document PDF"):
            quizgen.request(sheet)
        failed = QuizDraft.objects.create(
            guide=sheet, status=Status.FAILED, error="ce guide n'a pas de document PDF"
        )
        self.assertNotIn(failed, list(generation.retryable_quiz_failures()))
        manager = User.objects.create_superuser("chef", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(manager)
        self.assertNotContains(self.client.get(sheet.get_absolute_url()), "Générer le QCM")
        self.client.post(
            reverse("catalogue:review_draft", args=[failed.pk]), {"action": "relancer"}
        )
        self.assertEqual(QuizDraft.objects.get(pk=failed.pk).status, Status.DISCARDED)
        response = self.client.post(
            reverse("catalogue:review_request", args=[sheet.slug]), follow=True
        )
        self.assertContains(response, "pas de document PDF")
        self.assertFalse(QuizDraft.objects.filter(guide=sheet, status=Status.PENDING).exists())

    def test_too_few_valid_questions_fail(self):
        quizgen.request(self.guide)
        job = quizgen.take_next()
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(quizgen, "chat", return_value={"questions": []}):
            with self.assertRaises(quizgen.GenerationError):
                quizgen.generate(job)

    def test_long_text_target_is_a_goal_above_the_minimum(self):
        # 9 questions visées par série (long texte), mais le modèle ne renouvelle ses questions
        # qu'une fois
        given = set()
        def chat(messages, schema, temperature=0.4):
            if "reponses" in schema["properties"]:
                return fake_chat(messages, schema, temperature)
            level = next(
                n for n in quizgen.SERIES if f"niveau {n} sur 6" in messages[-1]["content"]
            )
            if level not in given:
                given.add(level)
                return fake_chat(messages, schema, temperature)
            return {"questions": []}
        quizgen.request(self.guide)
        with mock.patch.object(narration_text, "extract_sections", return_value=SECTIONS), \
                mock.patch.object(quizgen, "per_series", return_value=9), \
                mock.patch.object(quizgen, "chat", side_effect=chat):
            draft = quizgen.generate(quizgen.take_next())
        self.assertEqual(draft.status, Status.REVIEW)
        self.assertEqual(
            draft.questions.filter(series=1).count(), 6
        )  # 6 sur 9 : assez pour publier


class ReviewPageTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("gestion", password="Un-mot-de-passe-solide-42!")
        self.manager.groups.add(Group.objects.get_or_create(name=CONTENT_MANAGERS_GROUP)[0])
        self.learner = User.objects.create_user("apprenant", password="Un-mot-de-passe-solide-42!")
        self.guide = Guide.objects.create(title="Guide pare-feu", slug="pare-feu")
        self.draft = QuizDraft.objects.create(
            guide=self.guide, status=Status.REVIEW, llm_model="qwen3.5:9b"
        )
        for series in quizgen.SERIES:
            for position in range(1, 6):
                QuizDraftQuestion.objects.create(
                    draft=self.draft,
                    series=series,
                    position=position,
                    text=f"Question {series}.{position} sur le pare-feu ?",
                    choices=["Bonne", "Mauvaise 1", "Mauvaise 2", "Mauvaise 3"],
                    cross_check=position != 5,
                )

    def test_reserved_to_content_managers(self):
        self.client.force_login(self.learner)
        self.assertEqual(self.client.get(reverse("catalogue:review_list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("catalogue:dashboard")), "✍️ Relecture")
        self.client.force_login(self.manager)
        response = self.client.get(reverse("catalogue:review_list"))
        self.assertContains(response, "Guide pare-feu")
        self.assertContains(self.client.get(reverse("catalogue:dashboard")), "✍️ Relecture")

    def test_edit_accept_reject_and_publish(self):
        self.client.force_login(self.manager)
        question = self.draft.questions.get(series=1, position=1)
        url = reverse("catalogue:review_question", args=[question.pk])
        self.client.post(
            url,
            {
                "action": "enregistrer",
                "text": "Question corrigée sur le pare-feu ?",
                "correct": "Juste",
                "wrong_1": "Faux 1",
                "wrong_2": "Faux 2",
                "wrong_3": "",
                "explanation": "Car.",
                "source": "R1",
            },
        )
        question.refresh_from_db()
        self.assertEqual(
            (question.text, question.choices, question.decision, question.edited),
            (
                "Question corrigée sur le pare-feu ?",
                ["Juste", "Faux 1", "Faux 2"],
                Decision.ACCEPTED,
                True,
            ),
        )
        bad = self.client.post(
            url,
            {"action": "enregistrer", "text": "Question ?", "correct": "A", "wrong_1": "A"},
            follow=True,
        )
        self.assertContains(bad, "toutes différentes")

        action = reverse("catalogue:review_draft", args=[self.draft.pk])
        self.client.post(action, {"action": "accepter-verifiees"})
        self.assertEqual(
            self.draft.questions.filter(decision=Decision.ACCEPTED).count(), 24
        )  # 4 vérifiées × 6
        self.assertContains(
            self.client.post(action, {"action": "publier"}, follow=True), "il faut au moins"
        )
        self.draft.questions.update(decision=Decision.ACCEPTED)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            import_quizzes, "DATA_DIR", Path(folder)
        ):
            response = self.client.post(action, {"action": "publier"}, follow=True)
        self.assertContains(response, "publié")
        self.assertEqual(QuizQuestion.objects.filter(guide=self.guide).count(), 30)

    def test_one_question_at_a_time(self):
        self.client.force_login(self.manager)
        detail = reverse("catalogue:review_detail", args=[self.draft.pk])
        questions = list(self.draft.questions.all())
        response = self.client.get(detail)
        self.assertEqual(response.context["current"], questions[0])
        self.assertContains(response, "Question 1 sur 30")
        self.assertContains(
            response, 'name="text"', count=1
        )  # un seul formulaire de question affiché

        # Accepter enchaîne sur la prochaine question à valider (la 2 est déjà traitée : on la
        # saute).
        questions[1].decision = Decision.REJECTED
        questions[1].save()
        response = self.client.post(
            reverse("catalogue:review_question", args=[questions[0].pk]), {"action": "accepter"}
        )
        self.assertRedirects(response, f"{detail}?q={questions[2].pk}")
        self.assertEqual(
            self.client.get(f"{detail}?q={questions[7].pk}").context["current"], questions[7]
        )
        self.assertEqual(
            self.client.get(detail).context["current"], questions[2]
        )  # première encore à valider

        self.draft.questions.exclude(pk=questions[29].pk).update(decision=Decision.ACCEPTED)
        response = self.client.post(reverse("catalogue:review_question", args=[questions[29].pk]),
                                    {"action": "rejeter"}, follow=True)
        self.assertContains(response, "Toutes les questions ont été traitées")
        self.assertEqual(response.context["current"], questions[29])

    def test_same_queue_buttons_for_every_generation(self):
        self.client.force_login(self.manager)
        for n in range(12):
            other = Guide.objects.create(title=f"Autre guide {n:02d}", slug=f"autre-{n}")
            GuideDocument.objects.create(guide=other, label="PDF", file=f"guides/autre-{n}.pdf")
        for kind in ("qcm", "cours", "lexique"):  # mêmes boutons dans chaque onglet de la relecture
            response = self.client.get(reverse("catalogue:review_list"), {"type": kind})
            self.assertContains(response, "Générer les 10 prochains")
            self.assertContains(response, "Tout générer (")
        dashboard = self.client.get(reverse("catalogue:generation_dashboard"))
        self.assertContains(
            dashboard, "Générer les 10 prochains", count=4
        )  # QCM, cours, lexiques, audio

        url = reverse("catalogue:generation_action", args=["qcm"])
        next_url = reverse("catalogue:review_list") + "?type=qcm"
        response = self.client.post(url, {"action": "file", "nombre": "10", "next": next_url})
        self.assertRedirects(response, next_url)
        self.assertEqual(QuizDraft.objects.filter(status=Status.PENDING).count(), 10)
        self.assertTrue(
            QuizDraft.objects.filter(guide__slug="autre-0", status=Status.PENDING).exists()
        )  # ordre suivi
        self.client.post(url, {"action": "file", "nombre": "tout"})
        self.assertEqual(QuizDraft.objects.filter(status=Status.PENDING).count(), 12)
        self.assertContains(
            self.client.get(reverse("catalogue:review_list"), {"type": "qcm"}),
            "Rien à mettre en file",
        )
