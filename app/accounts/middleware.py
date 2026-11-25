"""
Contrôles faits à chaque requête d'un utilisateur connecté :
  - double authentification : tant qu'elle n'est pas passée (ou activée, pour un compte qui la
  doit), seules
    les pages qui y mènent sont accessibles ;
  - inactivité : un compte à droits élevés est déconnecté après PRIVILEGED_IDLE_MINUTES sans
  activité ;
  - politique de sécurité du contenu (CSP) et en-têtes associés, posés aussi par nginx en production
  :
    ils s'appliquent ainsi en développement et dans les tests.
"""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from . import audit, two_factor

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-src"
    " 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"
LAST_SEEN = "derniere_activite"

# Actions éditoriales journalisées, quelle que soit la vue (atelier, relecture des cours, podcasts,
# QCM, lexiques).
AUDITED_VERBS = {"publier": "publication", "renvoyer": "renvoi", "supprimer": "suppression"}
AUDITED_VIEWS = {("catalogue", "generation_action"), ("catalogue", "generation_queue_action"),
                 ("catalogue", "generation_queue")}


def _open_paths():
    """Pages accessibles avant le second facteur : le parcours d'activation/vérification et la
    déconnexion."""
    return (
        reverse("accounts:two_factor_verify"),
        reverse("accounts:two_factor_setup"),
        reverse("accounts:logout"),
        "/" + settings.STATIC_URL.lstrip("/"),
        "/healthz/",
    )


class SecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        blocked = self._check(request)
        response = blocked or self.get_response(request)
        if not blocked:
            self._audit(request, response)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response

    def _check(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        expired = self._idle(request, user)
        if expired:
            return expired
        if two_factor.verified(request) or request.path.startswith(_open_paths()):
            return None
        has_device = two_factor.has_device(request)
        if not has_device and not (settings.MFA_REQUIRED_FOR_PRIVILEGED and user.is_privileged):
            return None
        target = reverse(
            "accounts:two_factor_verify" if has_device else "accounts:two_factor_setup"
        )
        if request.method != "GET":
            if request.content_type == "application/json":
                return JsonResponse({"error": "Double authentification requise."}, status=403)
            return redirect(target)
        return redirect(f"{target}?{urlencode({'next': request.get_full_path()})}")

    @staticmethod
    def _audit(request, response):
        if (
            request.method != "POST"
            or response.status_code >= 400
            or not request.user.is_authenticated
        ):
            return
        match = request.resolver_match
        if match is None:
            return
        verb = request.POST.get("action", "")
        if (match.namespace, match.url_name) in AUDITED_VIEWS:
            action = "generation"
        elif verb in AUDITED_VERBS and match.namespace in (
            "studio",
            "courses",
            "podcasts",
            "catalogue",
        ):
            action = AUDITED_VERBS[verb]
        else:
            return
        target = " ".join(str(v) for v in match.kwargs.values()) or match.url_name
        audit.record(request, action, target=f"{match.namespace}:{match.url_name} {target}".strip(),
                     verbe=verb, chemin=request.path)

    @staticmethod
    def _idle(request, user):
        if not user.is_privileged:
            return None
        now, last = time.time(), request.session.get(LAST_SEEN)
        if last and now - last > settings.PRIVILEGED_IDLE_MINUTES * 60:
            audit.record(request, "deconnexion_inactivite", user=user)
            logout(request)
            messages.info(request, "Vous avez été déconnecté après une période d'inactivité.")
            return redirect(
                f"{reverse(settings.LOGIN_URL)}?{urlencode({'next': request.get_full_path()})}"
            )
        if not last or now - last > 60:  # au plus une écriture de session par minute
            request.session[LAST_SEEN] = now
        return None
