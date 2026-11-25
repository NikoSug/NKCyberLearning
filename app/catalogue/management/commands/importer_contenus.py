"""
Importe un pack de contenus (installation ou mise à jour) : voir catalogue/packs.py.
    python manage.py importer_contenus - < pack.tar
        # en flux : docker compose exec -T web … < pack.tar
    python manage.py importer_contenus pack.tar --essai
        # vérifie et dit ce qui changerait, sans rien enregistrer
    python manage.py importer_contenus pack.tar --forcer
        # écrase aussi les contenus modifiés sur cette instance
"""

import sys

from django.core.management.base import BaseCommand, CommandError

from catalogue import packs
from catalogue.models import ContentImport


class Command(BaseCommand):
    help = "Importe un pack de contenus sans toucher aux comptes, à la progression ni aux lectures."

    def add_arguments(self, parser):
        parser.add_argument(
            "source", nargs="?", help="fichier .tar, ou « - » pour l'entrée standard"
        )
        parser.add_argument(
            "--version-installee",
            action="store_true",
            help="afficher la version du dernier pack importé (vide si aucun) et s'arrêter",
        )
        parser.add_argument("--essai", action="store_true", help="vérifier sans rien enregistrer")
        parser.add_argument(
            "--forcer", action="store_true", help="écraser les contenus modifiés sur cette instance"
        )

    def handle(self, *args, source, essai, forcer, version_installee, **options):
        if version_installee:
            last = ContentImport.objects.first()
            self.stdout.write(last.version if last else "")
            return
        if not source:
            raise CommandError("Indiquez le pack à importer (fichier ou « - »).")
        try:
            if source == "-":
                manifest, report, media = packs.import_pack(
                    sys.stdin.buffer, force=forcer, dry_run=essai
                )
            else:
                with open(source, "rb") as handle:
                    manifest, report, media = packs.import_pack(handle, force=forcer, dry_run=essai)
        except packs.PackError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Pack {manifest['version']} (produit par {manifest.get('origin', '?')}, "
                          f"{manifest.get('built_at', '')[:10]}) : {media} fichiers vérifiés."
                          + (" ESSAI : rien n'a été enregistré." if essai else ""))
        problems = 0
        for spec in packs.SPECS:
            stats = report[spec.kind]
            self.stdout.write(
                f"  {spec.label:28} {stats['crees']:5} créés  {stats['mis_a_jour']:5} mis à jour  "
                f"{stats['inchanges']:5} inchangés  {len(stats['conserves']):4} conservés  "
                f"{len(stats['erreurs']):3} erreurs"
            )
            for line in stats["conserves"][:5] + stats["erreurs"][:5]:
                self.stdout.write(f"      · {line}")
            problems += len(stats["erreurs"])
        if problems:
            self.stdout.write(
                self.style.WARNING(f"{problems} contenu(s) en erreur : voir ci-dessus.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Import terminé." if not essai else "Essai terminé.")
            )
