"""
Génère les versions audio des guides (texte réécrit par l'IA locale, synthèse vocale Kyutai, service
« tts »).

    python manage.py narrate                  # traite la file d'attente puis s'arrête
    python manage.py narrate --boucle         # service « narrator » : surveille la file en continu
    python manage.py narrate --tout
        # met en file tous les guides en français (après les demandes)
    python manage.py narrate --guide <slug>   # met un guide en file (prioritaire)
"""

import time

from django.core.management.base import BaseCommand, CommandError

from catalogue import generation, narration
from catalogue.models import Guide

IDLE_SECONDS = 15


class Command(BaseCommand):
    help = "Génère les versions audio des guides (file d'attente)."

    def add_arguments(self, parser):
        parser.add_argument("--boucle", action="store_true", help="Surveiller la file en continu.")
        parser.add_argument(
            "--tout", action="store_true", help="Mettre en file tous les guides en français."
        )
        parser.add_argument("--guide", help="Mettre en file un guide (slug).")

    def handle(self, *args, **options):
        if options["guide"]:
            guide = Guide.objects.filter(slug=options["guide"]).first()
            if guide is None:
                raise CommandError(f"Guide introuvable : {options['guide']}")
            narration.request(guide)
            self.stdout.write(f"« {guide.title} » mis en file.")
        if options["tout"]:
            queued = narration.request_all()
            self.stdout.write(f"{queued} guide(s) en file (après les demandes des utilisateurs).")
        if options["guide"] or options["tout"]:
            if not options["boucle"]:
                return

        recovered = narration.recover_stale(immediate=options["boucle"])  # service : seul à générer
        if recovered:
            self.stdout.write(f"{recovered} génération(s) interrompue(s) remise(s) en file.")
        kind = generation.Kind.AUDIO
        while True:
            generation.heartbeat(kind)
            if generation.is_paused(kind):  # pause depuis le tableau de bord des générations
                if not options["boucle"]:
                    self.stdout.write(
                        self.style.WARNING(
                            "Génération audio en pause (tableau de bord des générations)."
                        )
                    )
                    return
                time.sleep(IDLE_SECONDS)
                continue
            if options["boucle"] and not generation.my_turn(kind):  # une génération à la fois
                time.sleep(IDLE_SECONDS)
                continue
            job = narration.take_next()
            if job is None:
                if not options["boucle"]:
                    self.stdout.write(self.style.SUCCESS("File d'attente vide."))
                    return
                time.sleep(IDLE_SECONDS)
                continue
            started = time.monotonic()
            self.stdout.write(f"→ {job.guide.title}")
            try:
                narration.generate(
                    job,
                    progress=lambda done, total, title: self.stdout.write(
                        f"   {done}/{total} {title[:70]}"
                    ),
                    checkpoint=lambda: generation.checkpoint(kind),
                )
            except generation.Paused:
                narration.suspend(job)
                self.stdout.write(
                    self.style.WARNING("   mise en pause : reprendra au chapitre en cours")
                )
            except (
                Exception
            ) as exc:  # NarrationError ou erreur inattendue : consignée, on passe à la suite
                expected = isinstance(exc, narration.NarrationError)
                narration.fail(job, exc if expected else f"erreur inattendue : {exc!r}")
                self.stderr.write(
                    (self.style.WARNING if expected else self.style.ERROR)(f"   échec : {exc!r}")
                )
                if generation.record_failure(kind, exc):
                    self.stderr.write(
                        self.style.ERROR(
                            "Trop d'échecs de suite : génération audio mise en pause (à reprendre"
                            " depuis le tableau de bord)."
                        )
                    )
            else:
                generation.record_success(kind)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"   prête : {job.duration_display} d'audio en"
                        f" {time.monotonic() - started:.0f} s"
                    )
                )
