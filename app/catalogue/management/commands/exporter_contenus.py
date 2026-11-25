"""
Produit un pack de contenus (tout le contenu publié, sans données personnelles) : voir
catalogue/packs.py.
    python manage.py exporter_contenus - > pack.tar
        # en flux, depuis l'hôte : docker compose exec -T web …
    python manage.py exporter_contenus /chemin/pack.tar --numero 2026.10
"""

import sys

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalogue import packs


class Command(BaseCommand):
    help = (
        "Exporte le contenu publié dans un pack (installation et mise à jour d'autres instances)."
    )

    def add_arguments(self, parser):
        parser.add_argument("destination", help="fichier .tar, ou « - » pour la sortie standard")
        parser.add_argument(
            "--numero", default="", help="numéro de version du pack (par défaut : date et heure)"
        )

    def handle(self, *args, destination, numero, **options):
        version = numero or timezone.localtime().strftime("%Y.%m.%d-%H%M")
        log = lambda message: sys.stderr.write(
            f"  {message}\n"
        )  # noqa: E731 (la sortie standard porte l'archive)
        if destination == "-":
            manifest = packs.export_pack(sys.stdout.buffer, version, log)
            sys.stdout.buffer.flush()
        else:
            with open(destination, "wb") as handle:
                manifest = packs.export_pack(handle, version, log)
        sys.stderr.write(
            f"Pack {version} : {manifest['files']} fichiers, {manifest['bytes'] / 1e9:.2f} Go.\n"
        )
