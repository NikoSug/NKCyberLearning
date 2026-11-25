from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import friends as service


@login_required
def friends_page(request):
    """Mes amis : classement de la semaine, invitations reçues et envoyées, ajout par pseudo."""
    rows = service.leaderboard(request.user)
    return render(request, "social/friends.html", {
        "rows": rows, "friend_count": len(rows) - 1,
        "incoming": service.incoming(request.user), "outgoing": service.outgoing(request.user),
        "week_start": service.week_start(),
    })


def _run(request, action, success):
    try:
        result = action()
    except service.FriendError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, success(result))
    return redirect("social:friends")


@login_required
@require_POST
def invite(request):
    return _run(request, lambda: service.invite(request.user, request.POST.get("username", "")),
                lambda r: (f"{r[0].username} et vous êtes maintenant amis !" if r[1] == "acceptee"
                           else f"Invitation envoyée à {r[0].username}."))


@login_required
@require_POST
def accept(request, pk):
    return _run(request, lambda: service.respond(request.user, pk, accept=True),
                lambda friend: f"{friend.username} et vous êtes maintenant amis !")


@login_required
@require_POST
def decline(request, pk):
    return _run(request, lambda: service.respond(request.user, pk, accept=False),
                lambda friend: f"Invitation de {friend.username} refusée.")


@login_required
@require_POST
def cancel(request, pk):
    return _run(request, lambda: service.cancel(request.user, pk), lambda _: "Invitation annulée.")


@login_required
@require_POST
def remove(request, user_id):
    return _run(
        request,
        lambda: service.remove(request.user, user_id),
        lambda _: "Ami retiré de votre liste.",
    )
