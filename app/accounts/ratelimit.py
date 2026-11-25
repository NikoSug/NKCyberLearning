"""
Limites de débit par utilisateur (ou par adresse IP pour un visiteur), dans le cache partagé : elles
complètent
celles de nginx (connexion, inscription) sur les pages coûteuses (recherche, IA, génération audio).
"""

import time
from functools import wraps

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from config.client_ip import get_client_ip


def hit(scope, who, limit, period):
    """Compte une requête ; False si la limite est dépassée sur la fenêtre en cours."""
    window = int(time.time() // period)
    key = f"rl:{scope}:{who}:{window}"
    if cache.add(key, 1, timeout=period + 5):
        return True
    try:
        return cache.incr(key) <= limit
    except ValueError:  # clé expirée entre-temps
        cache.add(key, 1, timeout=period + 5)
        return True


def rate_limit(scope, limit, period, methods=("GET", "POST")):
    """Décorateur : au-delà de `limit` requêtes par `period` secondes, réponse 429 (ou message et
    retour)."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method in methods:
                who = (
                    f"u{request.user.pk}"
                    if request.user.is_authenticated
                    else f"ip{get_client_ip(request)}"
                )
                if not hit(scope, who, limit, period):
                    text = "Trop de requêtes en peu de temps : patientez un peu avant de réessayer."
                    if request.content_type == "application/json" or request.headers.get(
                        "Accept", ""
                    ).startswith("application/json"):
                        return JsonResponse({"error": text}, status=429)
                    if (
                        request.method == "POST"
                    ):  # formulaire : retour à la page d'origine, avec le message
                        messages.error(request, text)
                        back = request.headers.get("Referer", "")
                        safe = url_has_allowed_host_and_scheme(
                            back, {request.get_host()}, request.is_secure()
                        )
                        return redirect(back if safe else "/")
                    return HttpResponse(text, status=429, content_type="text/plain; charset=utf-8")
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
