"""Invitations, liste d'amis et classement de la semaine entre amis."""
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from progression import avatars, items
from progression import xp as progression
from progression.models import LearnerProfile, XPEvent

from .models import Friendship

MAX_PENDING = 30   # invitations envoyées en attente, au plus
MAX_FRIENDS = 200
Status = Friendship.Status


class FriendError(Exception):
    pass


def _between(a, b):
    return Friendship.objects.filter(Q(requester=a, addressee=b) | Q(requester=b, addressee=a))


def friends(user):
    """Utilisateurs amis (invitation acceptée, dans un sens ou dans l'autre)."""
    links = Friendship.objects.filter(Q(requester=user) | Q(addressee=user), status=Status.ACCEPTED)
    ids = {f.addressee_id if f.requester_id == user.pk else f.requester_id for f in links}
    return list(get_user_model().objects.filter(pk__in=ids).order_by("username"))


def are_friends(a, b):
    return _between(a, b).filter(status=Status.ACCEPTED).exists()


def incoming(user):
    return list(
        Friendship.objects.filter(addressee=user, status=Status.PENDING).select_related("requester")
    )


def outgoing(user):
    return list(
        Friendship.objects.filter(requester=user, status=Status.PENDING).select_related("addressee")
    )


@transaction.atomic
def invite(user, username):
    """
    Envoie une invitation. Si l'autre personne nous avait déjà invités, l'amitié
    est acceptée directement. Renvoie (ami, "envoyee" | "acceptee").
    """
    username = (username or "").strip()
    target = get_user_model().objects.filter(username__iexact=username, is_active=True).first()
    if target is None:
        raise FriendError(f"Aucun utilisateur ne s'appelle « {username[:40]} ».")
    if target == user:
        raise FriendError("Vous ne pouvez pas vous ajouter vous-même.")
    existing = _between(user, target).select_for_update().first()
    if existing and existing.status == Status.ACCEPTED:
        raise FriendError(f"{target.username} fait déjà partie de vos amis.")
    if existing and existing.requester_id == user.pk:
        raise FriendError(f"Invitation déjà envoyée à {target.username}.")
    if existing:  # l'autre nous avait invités : on accepte
        existing.status, existing.accepted_at = Status.ACCEPTED, timezone.now()
        existing.save(update_fields=["status", "accepted_at"])
        return target, "acceptee"
    if Friendship.objects.filter(requester=user, status=Status.PENDING).count() >= MAX_PENDING:
        raise FriendError("Trop d'invitations en attente : attendez des réponses ou annulez-en.")
    if len(friends(user)) >= MAX_FRIENDS:
        raise FriendError(f"Vous avez atteint la limite de {MAX_FRIENDS} amis.")
    try:
        with transaction.atomic():
            Friendship.objects.create(requester=user, addressee=target)
    except IntegrityError as exc:  # invitation croisée envoyée au même instant
        raise FriendError("Invitation déjà en cours, rechargez la page.") from exc
    return target, "envoyee"


def respond(user, pk, accept):
    """Accepter ou refuser une invitation reçue."""
    invitation = (
        Friendship.objects.filter(pk=pk, addressee=user, status=Status.PENDING)
        .select_related("requester")
        .first()
    )
    if invitation is None:
        raise FriendError("Cette invitation n'existe plus.")
    if accept:
        invitation.status, invitation.accepted_at = Status.ACCEPTED, timezone.now()
        invitation.save(update_fields=["status", "accepted_at"])
    else:
        invitation.delete()
    return invitation.requester


def cancel(user, pk):
    """Annuler une invitation envoyée et pas encore acceptée."""
    deleted, _ = Friendship.objects.filter(pk=pk, requester=user, status=Status.PENDING).delete()
    if not deleted:
        raise FriendError("Cette invitation n'existe plus.")


def remove(user, friend_id):
    deleted, _ = Friendship.objects.filter(
        Q(requester=user, addressee_id=friend_id) | Q(requester_id=friend_id, addressee=user),
        status=Status.ACCEPTED).delete()
    if not deleted:
        raise FriendError("Cette personne ne fait pas partie de vos amis.")


# --- Classement -------------------------------------------------------------------------
@dataclass
class Row:
    user: object
    avatar: object
    week_xp: int
    total: int
    level: int
    title: str
    streak: int
    streak_today: bool
    is_me: bool = False
    rank: int = 0
    frame: object = None
    profile_title: object = None


def week_start(today=None):
    today = today or timezone.localdate()
    return today - timedelta(days=today.weekday())  # lundi


def leaderboard(user):
    """Moi et mes amis, classés par XP gagnés depuis lundi (puis par XP total)."""
    people = [user] + friends(user)
    ids = [p.pk for p in people]
    monday = week_start()
    week = dict(XPEvent.objects.filter(user_id__in=ids, created_at__date__gte=monday)
                .values("user").annotate(xp=Sum("amount")).values_list("user", "xp"))
    totals = dict(XPEvent.objects.filter(user_id__in=ids).values("user").annotate(xp=Sum("amount"))
                  .values_list("user", "xp"))
    profiles = {p["user"]: p for p in LearnerProfile.objects.filter(user_id__in=ids)
                .values("user", "avatar", "frame", "profile_title")}
    rows = []
    for person in people:
        total = totals.get(person.pk, 0)
        days, done_today = progression.streak_for(person)
        level = progression.level_for(total)
        chosen = profiles.get(person.pk, {})
        rows.append(
            Row(
                user=person,
                avatar=avatars.get(chosen.get("avatar", "")),
                week_xp=week.get(person.pk, 0),
                total=total,
                level=level,
                title=progression.level_title(level),
                streak=days,
                streak_today=done_today,
                is_me=person.pk == user.pk,
                frame=items.get(chosen.get("frame", ""), items.FRAME),
                profile_title=items.get(chosen.get("profile_title", ""), items.TITLE),
            )
        )
    rows.sort(key=lambda r: (-r.week_xp, -r.total, r.user.username.lower()))
    for rank, row in enumerate(rows, start=1):
        row.rank = rank
    return rows
