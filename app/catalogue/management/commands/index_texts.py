"""
Extrait et conserve le texte des guides (par chapitres) pour la recherche dans leur contenu.

    python manage.py index_texts
        # guides publiés avec un PDF dont le texte manque encore
    python manage.py index_texts --refaire
        # tout réextraire (après une amélioration de l'extraction)

Même texte que celui de l'assistant de lecture (qui l'extrait sinon à la première question).
"""

from django.core.management.base import BaseCommand

from catalogue import assistant, generation
from catalogue.models import GuideSection


class Command(BaseCommand):
    help = "Indexe le texte des guides pour la recherche dans leur contenu."

    def add_arguments(self, parser):
        parser.add_argument(
            "--refaire", action="store_true", help="Réextraire aussi les guides déjà indexés."
        )

    def handle(self, *args, **options):
        guides = generation.eligible_guides().order_by("title")
        if options["refaire"]:
            GuideSection.objects.filter(guide__in=guides).delete()
        missing = guides.exclude(pk__in=GuideSection.objects.values("guide"))
        done = failed = 0
        for guide in missing:
            try:
                count = len(assistant.sections(guide))
            except Exception as exc:  # PDF illisible ou absent : on continue avec les autres
                failed += 1
                self.stderr.write(self.style.WARNING(f"  {guide.title[:70]} : {exc}"))
                continue
            done += 1
            self.stdout.write(f"  {guide.title[:70]} : {count} chapitre(s)")
        self.stdout.write(
            self.style.SUCCESS(
                f"{done} guide(s) indexé(s), {failed} échec(s), "
                f"{guides.count() - missing.count()} déjà indexé(s)."
            )
        )
