"""Contrôle d'accès de toutes les URL : une nouvelle vue est protégée par défaut, sinon ce test
échoue."""

import re

from django.conf import settings
from django.shortcuts import resolve_url
from django.test import TestCase
from django.urls import URLResolver, get_resolver

from .models import User

# Pages ouvertes aux visiteurs non connectés (noms d'URL).
PUBLIC = {
    "healthcheck",
    "legal_mentions",
    "legal_privacy",
    "legal_terms",
    "legal_credits",
    "login",
    "signup",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
}

# Espaces réservés aux rédacteurs, gestionnaires de contenu ou administrateurs : refusés à un
# apprenant.
STAFF_PREFIXES = (
    "/atelier/",
    "/relecture/",
    "/generations/",
    "/cours/relecture/",
    "/podcasts/relecture/",
    "/compte/utilisateurs/",
)


def all_urls(patterns=None, prefix="/"):
    """(nom, URL d'exemple) pour chaque route du site, hors administration Django (protégée par
    elle-même)."""
    for pattern in get_resolver().url_patterns if patterns is None else patterns:
        if isinstance(pattern, URLResolver):
            if pattern.namespace != "admin":
                yield from all_urls(pattern.url_patterns, prefix + str(pattern.pattern))
        else:
            # « 1 » convient à tous les convertisseurs utilisés (int, slug, str).
            yield pattern.name, re.sub(r"<[^>]+>", "1", prefix + str(pattern.pattern))


class AccessTests(TestCase):
    def fetch(self, url):
        """GET, ou POST si la vue n'accepte que POST : on veut atteindre le contrôle d'accès."""
        response = self.client.get(url)
        return self.client.post(url) if response.status_code == 405 else response

    def test_anonymous_redirected_to_login(self):
        for name, url in all_urls():
            if name in PUBLIC:
                continue
            with self.subTest(url=url):
                response = self.fetch(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response["Location"].startswith(resolve_url(settings.LOGIN_URL)),
                    response["Location"],
                )

    def test_learner_refused_in_staff_areas(self):
        self.client.force_login(User.objects.create_user("apprenant"))
        for _name, url in all_urls():
            if url.startswith(STAFF_PREFIXES):
                with self.subTest(url=url):
                    self.assertEqual(self.fetch(url).status_code, 403)
