from .models import Friendship


def friend_requests(request):
    """Nombre d'invitations reçues en attente (pastille sur l'icône Amis)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    return {
        "friend_requests": (
            Friendship.objects.filter(addressee=user, status=Friendship.Status.PENDING).count()
        )
    }
