from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from . import legal


def healthcheck(_request):
    """Point de contrôle utilisé par Docker (ne révèle aucune information)."""
    return HttpResponse("ok", content_type="text/plain")


# L'URL de l'administration est configurable pour ne pas exposer /admin/ par défaut.
ADMIN_PATH = settings.ADMIN_URL_PATH

urlpatterns = [
    path("healthz/", healthcheck, name="healthcheck"),
    # Pages légales (sans compte)
    path("mentions-legales/", legal.page, {"name": "mentions"}, name="legal_mentions"),
    path("confidentialite/", legal.page, {"name": "confidentialite"}, name="legal_privacy"),
    path("conditions-utilisation/", legal.page, {"name": "conditions"}, name="legal_terms"),
    path("credits/", legal.page, {"name": "credits"}, name="legal_credits"),
    path("compte/", include("accounts.urls")),
    path("progression/", include("progression.urls")),
    path("amis/", include("social.urls")),
    path("cours/", include("courses.urls")),
    path("podcasts/", include("podcasts.urls")),
    path("atelier/", include("studio.urls")),
    path("", include("catalogue.urls")),
    path(ADMIN_PATH, admin.site.urls),
]

admin.site.site_header = f"{settings.INSTANCE_NAME} — Administration"
admin.site.site_title = settings.INSTANCE_NAME
admin.site.index_title = f"Gestion de la plateforme — version {settings.APP_VERSION}"
