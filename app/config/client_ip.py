"""Détermination de l'adresse IP réelle du client, pour django-axes."""


def get_client_ip(request):
    """
    Django n'est joignable que par nginx (réseau Docker interne), qui écrase
    l'en-tête X-Real-IP avec l'adresse de connexion réelle : on peut donc s'y
    fier. Sans nginx (développement), on retombe sur REMOTE_ADDR.
    """
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "")
