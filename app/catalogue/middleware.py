"""
Installation sans IA (AI_ENABLED=0, typiquement sans carte graphique) : l'application fonctionne,
mais tout ce qui
demanderait un travail à l'IA locale (Ollama) ou à la synthèse vocale est refusé ici, en un seul
endroit.
Les gabarits masquent les boutons correspondants (variable « ai_enabled ») ; ce filtre protège les
requêtes directes.
"""

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

MESSAGE = "L'IA n'est pas activée sur cette installation."

# Vues qui n'existent que pour l'IA, quelle que soit la méthode.
AI_VIEWS = {
    ("studio", "ai_stream"),
    ("studio", "ai_questions"),
    ("studio", "ai_check_episode"),
    ("studio", "qcm_ai"),
    ("catalogue", "guide_ask"),
    ("catalogue", "assistant_ask"),
    ("catalogue", "review_request"),
    ("catalogue", "generation_action"),
    ("catalogue", "generation_queue_action"),
}
# Actions (champ « action » d'un formulaire) qui lancent une génération ou un enregistrement.
AI_ACTIONS = {
    "*": {"relancer", "reecrire", "generer", "completer"},
    "podcasts": {"valider", "reenregistrer"},  # publier une série = l'enregistrer à deux voix
}
# Pages en lecture où un POST lance l'IA (demander la version audio d'un guide).
AI_POSTS = {("catalogue", "guide_listen")}


def needs_ai(request):
    match = request.resolver_match
    if match is None:
        return False
    key = (match.namespace, match.url_name)
    if key in AI_VIEWS:
        return True
    if request.method != "POST":
        return False
    action = request.POST.get("action", "")
    return (
        key in AI_POSTS
        or action in AI_ACTIONS["*"]
        or action in AI_ACTIONS.get(match.namespace, ())
        or (key == ("studio", "create") and request.POST.get("mode") == "ia")
    )


class AIAvailabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if settings.AI_ENABLED or not needs_ai(request):
            return None
        if request.content_type == "application/json" or request.headers.get(
            "Accept", ""
        ).startswith("application/json"):
            return JsonResponse({"error": MESSAGE}, status=503)
        messages.error(request, MESSAGE)
        back = request.headers.get("Referer", "")
        safe = url_has_allowed_host_and_scheme(back, {request.get_host()}, request.is_secure())
        return redirect(back if safe else "/")
