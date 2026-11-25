"""
Génère des brouillons de QCM avec l'IA locale (Ollama), à relire avant publication.

    python manage.py generate_quizzes --tout               # met en file tous les guides sans QCM
    python manage.py generate_quizzes --guide <slug>       # met un guide en file
    python manage.py generate_quizzes --referentiel <slug> # met un référentiel ANSSI en file (son
    PDF)
    python manage.py generate_quizzes --referentiels
        # met en file les référentiels ANSSI sans QCM
    python manage.py generate_quizzes                      # traite la file puis s'arrête
    python manage.py generate_quizzes --boucle             # service « quizgen » : surveille la file
    python manage.py generate_quizzes --parallele 2
        # plusieurs guides à la fois (OLLAMA_NUM_PARALLEL)
"""

import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection

from catalogue import generation, quizgen
from catalogue.models import Guide, Referential

IDLE_SECONDS = 20


class Command(BaseCommand):
    help = "Génère des brouillons de QCM avec l'IA locale (relecture avant publication)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tout", action="store_true", help="Mettre en file tous les guides sans QCM."
        )
        parser.add_argument("--guide", help="Mettre en file un guide (slug).")
        parser.add_argument("--referentiel", help="Mettre en file un référentiel ANSSI (slug).")
        parser.add_argument(
            "--referentiels",
            action="store_true",
            help="Mettre en file les référentiels ANSSI sans QCM.",
        )
        parser.add_argument("--boucle", action="store_true", help="Surveiller la file en continu.")
        parser.add_argument(
            "--parallele", type=int, default=2, help="Guides générés en même temps (défaut : 2)."
        )

    def handle(self, *args, **options):
        for model, slug in ((Guide, options["guide"]), (Referential, options["referentiel"])):
            if not slug:
                continue
            subject = model.objects.filter(slug=slug).first()
            if subject is None:
                raise CommandError(f"{model._meta.verbose_name.capitalize()} introuvable : {slug}")
            try:
                quizgen.request(subject)
            except quizgen.GenerationError as exc:
                raise CommandError(f"{subject} : {exc}") from exc
            self.stdout.write(f"« {subject} » mis en file.")
        if options["tout"]:
            guides = list(quizgen.guides_without_quiz())
            for guide in guides:
                quizgen.request(guide)
            self.stdout.write(f"{len(guides)} guide(s) sans QCM mis en file.")
        if options["referentiels"]:
            referentials = list(
                quizgen.referentials_with_pdf().filter(quiz_questions__isnull=True).distinct()
            )
            for referential in referentials:
                quizgen.request(referential)
            self.stdout.write(f"{len(referentials)} référentiel(s) sans QCM mis en file.")
        queued = (
            options["guide"] or options["referentiel"] or options["tout"] or options["referentiels"]
        )
        if queued and not options["boucle"]:
            return

        recovered = quizgen.recover_stale(immediate=options["boucle"])  # service : seul à générer
        if recovered:
            self.stdout.write(f"{recovered} génération(s) interrompue(s) remise(s) en file.")
        self.in_turn = options["boucle"]  # service : attend son tour (une génération à la fois)
        workers = max(1, min(options["parallele"], 8))
        self.stdout.write(f"Modèle : {quizgen.model_name()} · {workers} guide(s) en parallèle.")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while True:
                generation.heartbeat(generation.Kind.QUIZ, workers)
                results = list(pool.map(self.work_one, range(workers)))
                if not any(results):
                    if not options["boucle"]:
                        paused = generation.is_paused(generation.Kind.QUIZ)
                        self.stdout.write(
                            self.style.WARNING("Génération des QCM en pause.")
                            if paused
                            else self.style.SUCCESS("File d'attente vide.")
                        )
                        return
                    time.sleep(IDLE_SECONDS)

    def count_failure(self, error):
        if generation.record_failure(generation.Kind.QUIZ, error):
            self.stderr.write(
                self.style.ERROR(
                    "Trop d'échecs de suite : génération des QCM mise en pause (à reprendre depuis"
                    " le tableau de bord)."
                )
            )

    in_turn = False  # appel direct (tests, commande ponctuelle) : sans attendre son tour

    def work_one(self, _slot):
        """Prend un brouillon dans la file et le génère (dans un fil d'exécution)."""
        close_old_connections()
        try:
            if generation.is_paused(
                generation.Kind.QUIZ
            ):  # pause depuis le tableau de bord des générations
                return False
            if self.in_turn and not generation.my_turn(
                generation.Kind.QUIZ
            ):  # une autre génération a la main
                return False
            draft = quizgen.take_next()
            if draft is None:
                return False
            started = time.monotonic()
            title = str(draft.subject)[:60]
            self.stdout.write(f"→ {title}")
            try:
                quizgen.generate(
                    draft,
                    progress=lambda n: self.stdout.write(f"   {title} : série {n}/6"),
                    checkpoint=lambda: generation.checkpoint(generation.Kind.QUIZ),
                )
            except generation.Paused:
                quizgen.suspend(draft)
                self.stdout.write(
                    self.style.WARNING(f"   {title} : mis en pause (séries faites conservées)")
                )
                if generation.gaming():
                    generation.unload_model()  # la dernière série a pu recharger le modèle
            except quizgen.GenerationError as exc:
                quizgen.fail(draft, exc)
                self.stderr.write(self.style.WARNING(f"   {title} : échec ({exc})"))
                self.count_failure(exc)
            except Exception as exc:  # erreur inattendue : consignée, on passe au suivant
                quizgen.fail(draft, f"erreur inattendue : {exc!r}")
                self.stderr.write(self.style.ERROR(f"   {title} : erreur inattendue {exc!r}"))
                self.count_failure(exc)
            else:
                generation.record_success(generation.Kind.QUIZ)
                self.stdout.write(self.style.SUCCESS(
                    f"   {title} : prêt à relire en {time.monotonic() - started:.0f} s"))
            return True
        finally:
            connection.close()  # connexion propre à ce fil d'exécution : rouverte au prochain guide
