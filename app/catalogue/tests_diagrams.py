"""Schémas d'exemple des questions de QCM : syntaxe, dessin SVG, génération, relecture, atelier et
affichage."""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AUTHORS_GROUP, CONTENT_MANAGERS_GROUP

from . import diagrams, quizgen, reading
from .management.commands import import_quizzes
from .models import Guide, QuizDraft, QuizDraftQuestion, QuizQuestion

User = get_user_model()
PASSWORD = "Un-mot-de-passe-solide-42!"
NETWORK = """flowchart LR
%% accès distant d'un employé
ATT[Attaquant]:::danger -.->|courriel piégé| PC[Poste de l'employé]
PC -->|VPN| FW{Pare-feu} --> SRV[(Serveur de fichiers)]
FW -- journaux --> SIEM(Supervision):::ok
"""


class SyntaxTests(TestCase):
    def test_flowchart_is_parsed(self):
        diagram = diagrams.parse(NETWORK)
        self.assertEqual(diagram.direction, "LR")
        self.assertEqual([(n.id, n.label, n.shape, n.cls) for n in diagram.nodes.values()], [
            ("ATT", "Attaquant", "rect", "danger"), ("PC", "Poste de l'employé", "rect", ""),
            ("FW", "Pare-feu", "check", ""), ("SRV", "Serveur de fichiers", "db", ""),
            ("SIEM", "Supervision", "round", "ok")])
        self.assertEqual(
            [(e.source, e.target, e.label, e.style) for e in diagram.edges],
            [
                ("ATT", "PC", "courriel piégé", "dashed"),
                ("PC", "FW", "VPN", "arrow"),
                ("FW", "SRV", "", "arrow"),
                ("FW", "SIEM", "journaux", "arrow"),
            ],
        )
        self.assertEqual(diagrams.parse("graph TD\nA-->B").direction, "TD")
        self.assertEqual(
            diagrams.parse('A["Serveur (DMZ)"] --- B').nodes["A"].label, "Serveur (DMZ)"
        )

    def test_invalid_diagrams_are_explained(self):
        self.assertIsNone(diagrams.problem(""))
        self.assertIsNone(diagrams.problem(NETWORK))
        for text, message in (
            ("A[Poste]", "aucun lien"),
            ("flowchart LR\nsubgraph DMZ\nA --> B\nend", "subgraph"),
            ("A --> B\nclick A https://exemple.fr", "click"),
            ("A -->", "illisible"),
            ("A[] --> B", "libellé vide"),
            ("\n".join(f"N{n} --> N{n + 1}" for n in range(12)), "trop d'éléments"),
        ):
            self.assertIn(message, diagrams.problem(text), text)

    def test_labels_are_escaped_in_the_svg(self):
        svg = diagrams.render('A["<script>alert(1)</script>"] -->|<b>x</b>| B[Serveur & co]')
        self.assertTrue(svg.startswith('<figure class="quiz-diagram"><svg'))
        self.assertNotIn("<script>", svg)
        self.assertNotIn("<b>", svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertIn("Serveur &amp; co", svg)
        self.assertIn('aria-label="Schéma : ', svg)
        self.assertEqual(diagrams.render("pas un schéma"), "")

    def test_layout_follows_the_flow(self):
        boxes, _width, _height = diagrams.layout(
            diagrams.parse("A --> B --> C\nC --> A")
        )  # boucle : pas de blocage
        self.assertLess(boxes["A"].x, boxes["B"].x)
        self.assertLess(boxes["B"].x, boxes["C"].x)
        boxes, *_ = diagrams.layout(diagrams.parse("flowchart TD\nA --> B"))
        self.assertLess(boxes["A"].y, boxes["B"].y)


class GeneratedQuestionTests(TestCase):
    ITEM = {
        "question": (
            "Dans une PME, un employé reçoit un courriel piégé. Que doit-il faire en priorité ?"
        ),
        "bonne_reponse": "Le signaler au support informatique",
        "mauvaises_reponses": [
            "Répondre pour vérifier",
            "Le transférer à ses collègues",
            "Ouvrir la pièce jointe",
        ],
        "explication": "Le guide recommande de signaler tout courriel suspect.",
        "source": "R3",
    }

    def test_reference_to_the_text_is_removed(self):
        checked = quizgen.check_question(
            {
                **self.ITEM,
                "question": (
                    "Selon le texte, que doit faire un employé qui reçoit un courriel piégé ?"
                ),
            },
            [],
        )
        self.assertEqual(
            checked["text"], "Que doit faire un employé qui reçoit un courriel piégé ?"
        )
        self.assertEqual(
            quizgen.clean_question("Quelle mesure est citée dans le texte?"),
            "Quelle mesure est citée ?",
        )
        self.assertIsNone(
            quizgen.check_question(
                {**self.ITEM, "question": "Quel est l'objectif principal de ce document ?"}, []
            )
        )

    def test_diagram_is_kept_only_when_readable(self):
        self.assertEqual(quizgen.check_question(self.ITEM, [])["diagram"], "")
        with_diagram = quizgen.check_question({**self.ITEM, "schema": NETWORK}, [])
        self.assertEqual(with_diagram["diagram"], NETWORK.strip())
        self.assertEqual(
            quizgen.check_question({**self.ITEM, "schema": "aucun"}, [])["diagram"], ""
        )
        about_diagram = {
            **self.ITEM,
            "question": "D'après le schéma, quel élément filtre les flux du VPN ?",
        }
        self.assertIsNone(
            quizgen.check_question({**about_diagram, "schema": "<svg onload=alert(1)>"}, [])
        )
        self.assertIsNotNone(quizgen.check_question({**about_diagram, "schema": NETWORK}, []))
        backup = {
            **self.ITEM,
            "question": "Quel schéma de sauvegarde convient à une petite mairie ?",
        }
        self.assertIsNotNone(quizgen.check_question(backup, []))  # « schéma de… » : pas un dessin

    def test_prompt_asks_for_concrete_unambiguous_questions(self):
        for expected in (
            "situation concrète",
            "Une seule bonne réponse",
            "selon le texte",
            "schema",
        ):
            self.assertIn(expected, quizgen.SYSTEM_PROMPT)
        self.assertIn(
            "schema", quizgen.QUESTIONS_SCHEMA["properties"]["questions"]["items"]["properties"]
        )

    def test_prompt_example_passes_its_own_checks(self):
        example = json.loads(
            quizgen.SYSTEM_PROMPT.rsplit("\n", 1)[-1]
        )  # le modèle imite cet exemple
        checked = quizgen.check_question(example, [])
        self.assertTrue(checked["diagram"])
        self.assertIn("À retenir", checked["explanation"])
        self.assertNotIn(
            "schema", quizgen.QUESTIONS_SCHEMA["properties"]["questions"]["items"]["required"]
        )

    def test_import_validates_and_stores_diagrams(self):
        guide = Guide.objects.create(title="Guide VPN", slug="vpn")
        questions = [
            {"series": s, "question": f"Question {s}.{n} sur le VPN ?", "choices": ["A", "B", "C"]}
            for s in range(1, 7)
            for n in range(5)
        ]
        questions[0]["diagram"] = NETWORK
        import_quizzes.store(guide, import_quizzes.validate({"questions": questions}, "vpn.json"))
        self.assertEqual(QuizQuestion.objects.filter(guide=guide).exclude(diagram="").count(), 1)
        questions[1]["diagram"] = "subgraph X"
        with self.assertRaisesMessage(CommandError, "schéma invalide"):
            import_quizzes.validate({"questions": questions}, "vpn.json")


class DiagramScreensTests(TestCase):
    def setUp(self):
        self.guide = Guide.objects.create(title="Guide VPN", slug="vpn")

    def login(self, name, group=None):
        account = User.objects.create_user(name, password=PASSWORD)
        if group:
            account.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(account)
        return account

    def test_review_edits_and_previews_the_diagram(self):
        self.login("gestion", CONTENT_MANAGERS_GROUP)
        draft = QuizDraft.objects.create(guide=self.guide, status=QuizDraft.Status.REVIEW)
        question = QuizDraftQuestion.objects.create(
            draft=draft,
            series=1,
            text="Question sur le VPN ?",
            choices=["Bonne", "Mauvaise 1", "Mauvaise 2"],
        )
        url = reverse("catalogue:review_question", args=[question.pk])
        fields = {"action": "enregistrer", "text": "Question sur le VPN ?", "correct": "Bonne",
                  "wrong_1": "Mauvaise 1", "wrong_2": "Mauvaise 2"}
        response = self.client.post(url, {**fields, "diagram": "subgraph X"}, follow=True)
        self.assertContains(response, "Schéma non enregistré")
        question.refresh_from_db()
        self.assertEqual(question.diagram, "")
        self.client.post(url, {**fields, "diagram": NETWORK})
        question.refresh_from_db()
        self.assertEqual(question.diagram, NETWORK.strip())
        page = self.client.get(
            reverse("catalogue:review_detail", args=[draft.pk]) + f"?q={question.pk}"
        )
        self.assertContains(page, 'name="diagram"')
        self.assertContains(page, '<figure class="quiz-diagram">')

    def test_author_adds_a_diagram_in_the_studio(self):
        self.login("redac", AUTHORS_GROUP)
        draft = QuizDraft.objects.create(guide=self.guide, status=QuizDraft.Status.WRITING,
                                         author=User.objects.get(username="redac"))
        url = reverse("studio:qcm_question", args=[draft.pk])
        question = {
            "serie": 1,
            "text": "Que filtre le pare-feu sur ce schéma ?",
            "correct": "Les flux du VPN",
            "wrong_1": "Les fichiers",
            "wrong_2": "Les mots de passe",
        }
        self.client.post(url, {**question, "diagram": "A -->"})
        self.assertFalse(draft.questions.exists())
        self.client.post(url, {**question, "diagram": NETWORK})
        self.assertEqual(draft.questions.get().diagram, NETWORK.strip())
        page = self.client.get(reverse("studio:qcm_edit", args=[draft.pk]) + "?serie=1")
        self.assertContains(page, '<figure class="quiz-diagram">')
        self.assertContains(page, "Comment écrire un schéma")

    def test_learner_sees_the_diagram_only_where_there_is_one(self):
        user = self.login("alice")
        for position in range(5):
            QuizQuestion.objects.create(
                guide=self.guide,
                series=1,
                position=position,
                text=f"Question {position} ?",
                choices=["A", "B", "C"],
                answer=0,
                diagram=NETWORK if position == 0 else "",
            )
        reading.mark_finished(user, self.guide)
        url = reverse("catalogue:quiz_take", args=["vpn", 1])
        page = self.client.get(url)
        self.assertContains(page, '<figure class="quiz-diagram">', count=1)
        self.assertContains(page, "Poste de l&#x27;employé")
        results = self.client.post(url, {f"q{q.pk}": "0" for q in QuizQuestion.objects.all()})
        self.assertContains(results, '<figure class="quiz-diagram">', count=1)
