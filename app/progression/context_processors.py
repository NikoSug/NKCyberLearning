from . import xp


def progression(request):
    """Barre d'XP de l'en-tête : calculée une seule fois par requête, pour les utilisateurs
    connectés."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    if not hasattr(request, "_xp_summary"):
        request._xp_summary = xp.summary(user)
    return {"xp": request._xp_summary}
