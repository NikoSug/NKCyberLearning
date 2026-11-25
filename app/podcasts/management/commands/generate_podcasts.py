"""
Génère des podcasts avec l'IA locale (scripts relus avant l'enregistrement à deux voix).

    python manage.py generate_podcasts --themes           # met en file une série par thème
    python manage.py generate_podcasts --theme <slug>     # un thème (slug de la catégorie)
    python manage.py generate_podcasts                    # traite la file puis s'arrête
    python manage.py generate_podcasts --boucle
        # service « podcastgen » : surveille la file
"""

import time

from django.core.management.base import BaseCommand, CommandError

from catalogue import generation, narration
from catalogue.models import GuideCategory
from podcasts import podcastgen
from podcasts.models import Series

IDLE_SECONDS = 20
KIND = generation.Kind.PODCAST


class Command(BaseCommand):
    help = "Génère des podcasts avec l'IA locale (relecture avant l'enregistrement)."

    def add_arguments(self, parser):
        parser.add_argument("--themes", action="store_true", help="Une série pour chaque thème.")
        parser.add_argument("--theme", help="Mettre en file la série d'un thème (slug).")
        parser.add_argument("--boucle", action="store_true", help="Surveiller la file en continu.")

    def handle(self, *args, **options):
        queued = False
        if options["theme"]:
            category = GuideCategory.objects.filter(slug=options["theme"]).first()
            if category is None:
                raise CommandError(f"Thème introuvable : {options['theme']}")
            podcastgen.request_theme(category)
            queued = True
        if options["themes"]:
            self.stdout.write(f"{len(podcastgen.request_all())} série(s) mise(s) en file.")
            queued = True
        if queued and not options["boucle"]:
            return

        recovered = podcastgen.recover_stale(
            immediate=options["boucle"]
        )  # service : seul à générer
        if recovered:
            self.stdout.write(f"{recovered} génération(s) interrompue(s) remise(s) en file.")
        while True:
            generation.heartbeat(KIND, 1)
            if generation.is_paused(KIND):
                if not options["boucle"]:
                    self.stdout.write(self.style.WARNING("Génération des podcasts en pause."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            if options["boucle"] and not generation.my_turn(KIND):  # une génération à la fois
                time.sleep(IDLE_SECONDS)
                continue
            series = podcastgen.take_next()
            if series is None:
                if not options["boucle"]:
                    self.stdout.write(self.style.SUCCESS("File d'attente vide."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            self.work(series)

    def work(self, series):
        started = time.monotonic()
        title = series.title[:60]
        recording = series.status == Series.Status.RECORDING
        self.stdout.write(f"→ {title} ({'enregistrement' if recording else 'écriture'})")
        try:
            (podcastgen.record if recording else podcastgen.generate)(
                series, progress=lambda label: self.stdout.write(f"   {title} : {label}"),
                checkpoint=lambda: generation.checkpoint(KIND))
        except generation.Paused:
            podcastgen.suspend(series)
            self.stdout.write(
                self.style.WARNING(f"   {title} : mis en pause (travail fait conservé)")
            )
            if generation.gaming():
                generation.unload_model()
                narration.release_voice()
        except (
            Exception
        ) as exc:  # GenerationError ou erreur inattendue : consignée, on passe à la suite
            expected = isinstance(exc, podcastgen.GenerationError)
            podcastgen.fail(series, exc if expected else f"erreur inattendue : {exc!r}")
            self.stderr.write(self.style.WARNING(f"   {title} : échec ({exc!r})"))
            if generation.record_failure(KIND, exc):
                self.stderr.write(
                    self.style.ERROR(
                        "Trop d'échecs de suite : génération des podcasts mise en pause (à"
                        " reprendre depuis le tableau de bord)."
                    )
                )
        else:
            generation.record_success(KIND)
            done = (
                "publiée" if recording else f"prête à relire ({series.episodes.count()} épisodes)"
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"   {series.title[:60]} : {done} en {time.monotonic() - started:.0f} s"
                )
            )
