"""
Récupère les actualités cyber et les fuites de données affichées sur le tableau de bord.

    python manage.py fetch_news            # une fois
    python manage.py fetch_news --boucle   # toutes les heures (service Docker « news »)
"""
import time

from django.core.management.base import BaseCommand

from catalogue import news


class Command(BaseCommand):
    help = "Récupère les actualités cyber (CERT-FR, presse) et les fuites de données récentes."

    def add_arguments(self, parser):
        parser.add_argument("--boucle", action="store_true", help="Relancer indéfiniment.")
        parser.add_argument(
            "--intervalle", type=int, default=3600, help="Secondes entre deux passages."
        )

    def handle(self, *args, **options):
        while True:
            for key, result in news.refresh_all().items():
                if isinstance(result, Exception):
                    self.stderr.write(self.style.WARNING(f"{key} : {result}"))
                else:
                    self.stdout.write(f"{key} : {result} entrée(s)")
            if not options["boucle"]:
                return
            time.sleep(max(options["intervalle"], 300))
