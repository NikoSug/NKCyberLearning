"""
Échange avec l'agent du poste (scripts/ia-agent.ps1), qui lance ou arrête les services de
génération.

    python manage.py service_requests --poll
        # signale l'agent ; affiche « <id> <action> » s'il y a une demande
    python manage.py service_requests --done <id> --ok "…"     # compte rendu d'une demande exécutée
    python manage.py service_requests --done <id> --error "…"
"""

from django.core.management.base import BaseCommand

from catalogue import generation


class Command(BaseCommand):
    help = "Demandes de lancement / d'arrêt des services, pour l'agent du poste."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll", action="store_true", help="Prendre la prochaine demande en attente."
        )
        parser.add_argument("--done", type=int, help="Identifiant de la demande exécutée.")
        result = parser.add_mutually_exclusive_group()
        result.add_argument(
            "--ok", nargs="?", const="", help="La demande a réussi (compte rendu facultatif)."
        )
        result.add_argument(
            "--error", nargs="?", const="", help="La demande a échoué (compte rendu)."
        )

    def handle(self, *args, **options):
        if options["poll"]:
            command = generation.take_command()
            if command:
                self.stdout.write(f"{command.pk} {command.action}")
        if options["done"]:
            ok = options["error"] is None
            generation.finish_command(
                options["done"], ok, (options["ok"] if ok else options["error"]) or ""
            )
