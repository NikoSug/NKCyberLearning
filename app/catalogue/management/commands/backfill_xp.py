"""Attribue les XP des lectures, tests et révisions faits avant la mise en place de la
progression."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from catalogue import rewards


class Command(BaseCommand):
    help = "Attribue rétroactivement les XP des activités passées (sans effet si déjà fait)."

    def handle(self, *args, **options):
        total = 0
        for user in get_user_model().objects.all():
            gained = rewards.backfill(user)
            total += gained
            if gained:
                self.stdout.write(f"{user.username} : +{gained} XP")
        self.stdout.write(self.style.SUCCESS(f"{total} XP attribués."))
