"""
Charge les tests de connaissances depuis catalogue/data/quiz/<identifiant ANSSI du guide>.json.

Format d'un fichier :
    {"guide": "<anssi_id>",
     "questions": [{"series": 1, "question": "…", "choices": ["bonne réponse", "leurre", …],
                    "explanation": "…", "source": "R12", "diagram": "A[Poste] --> B[Serveur]"}, …]}

« diagram » (schéma d'exemple, syntaxe décrite dans catalogue/diagrams.py) est facultatif.

La première proposition est toujours la bonne réponse dans le fichier (plus simple à
rédiger et à relire) ; l'import mélange les propositions de façon déterministe pour
que la bonne réponse ne soit pas toujours à la même place.
"""

import hashlib
import json
import random
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalogue import diagrams
from catalogue.models import Guide, QuizQuestion, subject_filter

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "quiz"
SERIES = range(1, 7)
MIN_PER_SERIES = 5


def validate(data, name):
    questions = data.get("questions") or []
    errors = []
    for index, q in enumerate(questions, start=1):
        choices = q.get("choices") or []
        if q.get("series") not in SERIES:
            errors.append(f"question {index} : série invalide")
        if not q.get("question", "").strip():
            errors.append(f"question {index} : énoncé vide")
        if not 3 <= len(choices) <= 5 or len(set(choices)) != len(choices):
            errors.append(f"question {index} : 3 à 5 propositions distinctes attendues")
        diagram_problem = diagrams.problem(q.get("diagram"))
        if diagram_problem:
            errors.append(f"question {index} : schéma invalide ({diagram_problem})")
    for number in SERIES:
        count = sum(q.get("series") == number for q in questions)
        if count < MIN_PER_SERIES:
            errors.append(f"série {number} : {count} question(s), {MIN_PER_SERIES} minimum")
    if errors:
        raise CommandError(f"{name} :\n  " + "\n  ".join(errors))
    return questions


def shuffled(question):
    """Mélange stable : la même question donne toujours le même ordre."""
    choices = list(question["choices"])
    seed = int(hashlib.sha256(question["question"].encode()).hexdigest(), 16)
    order = list(range(len(choices)))
    random.Random(seed).shuffle(order)  # nosec B311 (aucun usage cryptographique)
    return [choices[i] for i in order], order.index(0)


@transaction.atomic
def store(guide, questions):
    """
    Enregistre les questions d'un guide ou d'un référentiel (format du fichier : première
    proposition = bonne réponse).
    Mise à jour en place (série, position) : les identifiants des questions restent stables,
    ce qui préserve l'historique des réponses utilisé par la révision du jour.
    """
    positions, kept = {}, []
    for q in questions:
        choices, answer = shuffled(q)
        positions[q["series"]] = positions.get(q["series"], 0) + 1
        question, _ = QuizQuestion.objects.update_or_create(
            **subject_filter(guide),
            series=q["series"],
            position=positions[q["series"]],
            defaults={
                "text": q["question"].strip(),
                "choices": choices,
                "answer": answer,
                "explanation": q.get("explanation", "").strip(),
                "source": q.get("source", "")[:120],
                "diagram": (q.get("diagram") or "").strip(),
            },
        )
        kept.append(question.pk)
    guide.quiz_questions.exclude(pk__in=kept).delete()


class Command(BaseCommand):
    help = "Importe les tests de connaissances (QCM) des guides depuis catalogue/data/quiz/."

    def add_arguments(self, parser):
        parser.add_argument(
            "fichiers", nargs="*", help="Limiter à certains fichiers (nom sans .json)."
        )

    def handle(self, *args, **options):
        files = sorted(DATA_DIR.glob("*.json"))
        if options["fichiers"]:
            files = [f for f in files if f.stem in options["fichiers"]]
        total = 0
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            guide = Guide.objects.filter(anssi_id=data.get("guide", path.stem)).first()
            if guide is None:
                self.stderr.write(self.style.WARNING(f"{path.name} : guide inconnu, ignoré."))
                continue
            questions = validate(data, path.name)
            store(guide, questions)
            total += len(questions)
            self.stdout.write(f"{guide.title[:70]} : {len(questions)} questions")
        self.stdout.write(
            self.style.SUCCESS(f"{len(files)} fichier(s), {total} questions importées.")
        )
