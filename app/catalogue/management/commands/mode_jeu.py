"""
Mode jeu : coupe l'IA locale et les générations (pour libérer le PC), puis les relance.

    python manage.py mode_jeu on
        # générations en pause, assistant coupé, modèle déchargé du GPU
    python manage.py mode_jeu off     # tout reprend comme avant
    python manage.py mode_jeu         # état actuel
"""

from django.core.management.base import BaseCommand

from catalogue import generation


class Command(BaseCommand):
    help = "Active ou désactive le mode jeu (coupe l'IA locale et les générations)."

    def add_arguments(self, parser):
        parser.add_argument(
            "etat", nargs="?", choices=["on", "off"], help="on : activer, off : désactiver."
        )

    def handle(self, *args, **options):
        if options["etat"]:
            generation.set_gaming(options["etat"] == "on")
        if generation.gaming():
            self.stdout.write(
                self.style.WARNING(
                    "Mode jeu activé : générations en pause, assistant coupé, modèle d'IA déchargé"
                    " du GPU."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Mode jeu désactivé : l'IA et les générations fonctionnent.")
            )
