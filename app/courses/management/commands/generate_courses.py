"""
Génère des cours avec l'IA locale (Ollama), à relire avant publication.

    python manage.py generate_courses --themes            # met en file un cours par thème
    python manage.py generate_courses --guides
        # met en file un cours par guide (français, avec PDF)
    python manage.py generate_courses --theme <slug>      # un thème (slug de la catégorie)
    python manage.py generate_courses --guide <slug>      # un guide
    python manage.py generate_courses                     # traite la file puis s'arrête
    python manage.py generate_courses --boucle
        # service « coursegen » : surveille la file
"""

import time

from django.core.management.base import BaseCommand, CommandError

from catalogue import generation
from catalogue.models import Guide, GuideCategory
from courses import coursegen
from courses.models import Course

IDLE_SECONDS = 20
KIND = generation.Kind.COURSE


class Command(BaseCommand):
    help = "Génère des cours avec l'IA locale (relecture avant publication)."

    def add_arguments(self, parser):
        parser.add_argument("--themes", action="store_true", help="Un cours pour chaque thème.")
        parser.add_argument("--guides", action="store_true", help="Un cours pour chaque guide.")
        parser.add_argument("--theme", help="Mettre en file le cours d'un thème (slug).")
        parser.add_argument("--guide", help="Mettre en file le cours d'un guide (slug).")
        parser.add_argument("--boucle", action="store_true", help="Surveiller la file en continu.")

    def handle(self, *args, **options):
        queued = False
        if options["theme"]:
            category = GuideCategory.objects.filter(slug=options["theme"]).first()
            if category is None:
                raise CommandError(f"Thème introuvable : {options['theme']}")
            coursegen.request_theme(category)
            queued = True
        if options["guide"]:
            guide = Guide.objects.filter(slug=options["guide"]).first()
            if guide is None:
                raise CommandError(f"Guide introuvable : {options['guide']}")
            coursegen.request_guide(guide)
            queued = True
        for flag, kind in (("themes", Course.Kind.THEME), ("guides", Course.Kind.GUIDE)):
            if options[flag]:
                self.stdout.write(
                    f"{len(coursegen.request_all(kind))} cours mis en file ({kind.label.lower()})."
                )
                queued = True
        if queued and not options["boucle"]:
            return

        recovered = coursegen.recover_stale(immediate=options["boucle"])  # service : seul à générer
        if recovered:
            self.stdout.write(f"{recovered} génération(s) interrompue(s) remise(s) en file.")
        while True:
            generation.heartbeat(KIND, 1)
            if generation.is_paused(KIND):
                if not options["boucle"]:
                    self.stdout.write(self.style.WARNING("Génération des cours en pause."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            if options["boucle"] and not generation.my_turn(KIND):  # une génération à la fois
                time.sleep(IDLE_SECONDS)
                continue
            course = coursegen.take_next()
            if course is None:
                if not options["boucle"]:
                    self.stdout.write(self.style.SUCCESS("File d'attente vide."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            self.work(course)

    def work(self, course):
        started = time.monotonic()
        title = course.title[:60]
        self.stdout.write(f"→ {title}")
        try:
            coursegen.generate(
                course,
                progress=lambda label: self.stdout.write(f"   {title} : {label}"),
                checkpoint=lambda: generation.checkpoint(KIND),
            )
        except generation.Paused:
            coursegen.suspend(course)
            self.stdout.write(
                self.style.WARNING(f"   {title} : mis en pause (leçons faites conservées)")
            )
            if generation.gaming():
                generation.unload_model()
        except (
            Exception
        ) as exc:  # GenerationError ou erreur inattendue : consignée, on passe au suivant
            expected = isinstance(exc, coursegen.GenerationError)
            coursegen.fail(course, exc if expected else f"erreur inattendue : {exc!r}")
            self.stderr.write(self.style.WARNING(f"   {title} : échec ({exc!r})"))
            if generation.record_failure(KIND, exc):
                self.stderr.write(
                    self.style.ERROR(
                        "Trop d'échecs de suite : génération des cours mise en pause (à reprendre"
                        " depuis le tableau de bord)."
                    )
                )
        else:
            generation.record_success(KIND)
            self.stdout.write(self.style.SUCCESS(
                f"   {course.title[:60]} : prêt à relire ({course.lessons.count()} leçons) en "
                f"{time.monotonic() - started:.0f} s"))
