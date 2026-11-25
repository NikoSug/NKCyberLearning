"""
Génère les lexiques des guides (sigles et termes techniques) avec l'IA locale, à relire avant
publication.

    python manage.py generate_glossaries --tout
        # met en file tous les guides sans lexique
    python manage.py generate_glossaries --guide <slug>    # met un guide en file
    python manage.py generate_glossaries                   # traite la file puis s'arrête
    python manage.py generate_glossaries --boucle
        # service « glossarygen » : surveille la file
"""

import time

from django.core.management.base import BaseCommand, CommandError

from catalogue import generation, glossary
from catalogue.models import Guide

IDLE_SECONDS = 20
KIND = generation.Kind.GLOSSARY


class Command(BaseCommand):
    help = "Génère les lexiques des guides avec l'IA locale (relecture avant publication)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tout", action="store_true", help="Mettre en file tous les guides sans lexique."
        )
        parser.add_argument("--guide", help="Mettre en file un guide (slug).")
        parser.add_argument("--boucle", action="store_true", help="Surveiller la file en continu.")

    def handle(self, *args, **options):
        if options["guide"]:
            guide = Guide.objects.filter(slug=options["guide"]).first()
            if guide is None:
                raise CommandError(f"Guide introuvable : {options['guide']}")
            glossary.request(guide)
            self.stdout.write(f"« {guide.title} » mis en file.")
        if options["tout"]:
            self.stdout.write(f"{len(glossary.request_all())} guide(s) mis en file.")
        if (options["guide"] or options["tout"]) and not options["boucle"]:
            return

        recovered = glossary.recover_stale(immediate=options["boucle"])  # service : seul à générer
        if recovered:
            self.stdout.write(f"{recovered} génération(s) interrompue(s) remise(s) en file.")
        while True:
            generation.heartbeat(KIND, 1)
            if generation.is_paused(KIND):
                if not options["boucle"]:
                    self.stdout.write(self.style.WARNING("Génération des lexiques en pause."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            if options["boucle"] and not generation.my_turn(KIND):  # une génération à la fois
                time.sleep(IDLE_SECONDS)
                continue
            job = glossary.take_next()
            if job is None:
                if not options["boucle"]:
                    self.stdout.write(self.style.SUCCESS("File d'attente vide."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            self.work(job)

    def work(self, job):
        started = time.monotonic()
        title = job.guide.title[:60]
        self.stdout.write(f"→ {title}")
        try:
            glossary.generate(
                job,
                progress=lambda label: self.stdout.write(f"   {title} : {label}"),
                checkpoint=lambda: generation.checkpoint(KIND),
            )
        except generation.Paused:
            glossary.suspend(job)
            self.stdout.write(
                self.style.WARNING(f"   {title} : mis en pause (parties analysées conservées)")
            )
            if generation.gaming():
                generation.unload_model()
        except (
            Exception
        ) as exc:  # GenerationError ou erreur inattendue : consignée, on passe au suivant
            expected = isinstance(exc, glossary.GenerationError)
            glossary.fail(job, exc if expected else f"erreur inattendue : {exc!r}")
            self.stderr.write(self.style.WARNING(f"   {title} : échec ({exc!r})"))
            if generation.record_failure(KIND, exc):
                self.stderr.write(
                    self.style.ERROR(
                        "Trop d'échecs de suite : génération des lexiques mise en pause (à"
                        " reprendre depuis le tableau de bord)."
                    )
                )
        else:
            generation.record_success(KIND)
            self.stdout.write(
                self.style.SUCCESS(
                    f"   {title} : prêt à relire ({job.entries.count()} entrées) en"
                    f" {time.monotonic() - started:.0f} s"
                )
            )
