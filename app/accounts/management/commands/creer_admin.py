"""
Crée (ou retrouve) le compte administrateur d'une nouvelle instance et affiche un lien pour choisir
son mot de
passe : aucun mot de passe ne transite par le terminal, les journaux ou l'historique du shell.
Usage : manage.py creer_admin <pseudo> --url https://formation.exemple.fr [--email admin@exemple.fr]
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts import audit


class Command(BaseCommand):
    help = (
        "Crée le compte administrateur et affiche son lien d'invitation (3 jours, un seul usage)."
    )

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--url",
            required=True,
            help="adresse publique de l'instance, ex. https://formation.exemple.fr",
        )
        parser.add_argument("--email", default="")

    def handle(self, *args, username, url, email, **options):
        if not url.startswith(("https://", "http://")):
            raise CommandError("--url doit commencer par https://")
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username, defaults={"email": email})
        if created:
            user.set_unusable_password()
        user.is_staff = user.is_superuser = user.is_active = True
        user.save()
        audit.record(
            None,
            "compte_cree" if created else "lien_mot_de_passe",
            target=username,
            user=None,
            username="installation",
            role="Administrateur",
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        link = url.rstrip("/") + reverse("accounts:password_reset_confirm",
                                         args=[uid, default_token_generator.make_token(user)])
        self.stdout.write(link)
