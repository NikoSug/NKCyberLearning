"""
Boutique : achat et choix des avatars et des objets (cadres, thèmes, titres, bonus),
payés en émeraudes.

L'XP n'est jamais dépensé : il mesure la progression (niveau). Le niveau sert
en revanche à débloquer les récompenses : chaque article a un niveau minimum.
Le profil est verrouillé pendant chaque achat : deux achats simultanés ne
peuvent pas dépenser deux fois les mêmes émeraudes.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import avatars, emeralds, items
from .models import AvatarPurchase, EmeraldEvent, ItemPurchase, LearnerProfile
from .xp import level_for, total_xp


class ShopError(Exception):
    pass


def owned(user):
    """Clés des avatars utilisables : achetés + offerts."""
    keys = set(AvatarPurchase.objects.filter(user=user).values_list("avatar", flat=True))
    return keys | {a.key for a in avatars.AVATARS if a.price == 0}


@transaction.atomic
def buy(user, key):
    """
    Achète un avatar et l'équipe. Le profil est verrouillé pendant l'achat : deux
    achats simultanés ne peuvent pas dépenser deux fois les mêmes émeraudes.
    """
    avatar = avatars.get(key)
    if avatar is None:
        raise ShopError("Cet avatar n'existe pas.")
    profile, _ = LearnerProfile.objects.get_or_create(user=user)
    profile = LearnerProfile.objects.select_for_update().get(pk=profile.pk)
    if key in owned(user):
        raise ShopError("Vous possédez déjà cet avatar.")
    if level_for(total_xp(user)) < avatar.min_level:
        raise ShopError(f"Cet avatar se débloque au niveau {avatar.min_level}.")
    available = emeralds.balance(user)
    if available < avatar.price:
        raise ShopError(
            f"Il vous manque {avatar.price - available}"
            f" émeraude{'s' if avatar.price - available > 1 else ''}."
        )
    emeralds.credit(
        user,
        f"achat:avatar:{key}",
        -avatar.price,
        EmeraldEvent.Kind.PURCHASE,
        f"Avatar « {avatar.name} »",
    )
    AvatarPurchase.objects.create(user=user, avatar=key, price=avatar.price)
    profile.avatar = key
    profile.save(update_fields=["avatar"])
    return avatar


def equip(user, key):
    """Choisit un avatar possédé ; une clé vide revient à l'initiale du pseudo."""
    if key and key not in owned(user):
        raise ShopError("Vous ne possédez pas cet avatar.")
    LearnerProfile.objects.update_or_create(user=user, defaults={"avatar": key or ""})


# --- Objets : cadres, thèmes, titres et bonus -------------------------------------------
PROFILE_FIELDS = {items.FRAME: "frame", items.THEME: "theme", items.TITLE: "profile_title"}


def owned_items(user):
    """Objets achetés, plus les objets offerts (prix nul)."""
    bought = set(ItemPurchase.objects.filter(user=user).values_list("item", flat=True))
    return bought | {item.key for item in items.ITEMS if item.price == 0}


def _pay(user, item, ref):
    """Vérifie niveau et solde puis débite. À appeler avec le profil verrouillé."""
    if level_for(total_xp(user)) < item.min_level:
        raise ShopError(f"« {item.name} » se débloque au niveau {item.min_level}.")
    available = emeralds.balance(user)
    if available < item.price:
        missing = item.price - available
        raise ShopError(f"Il vous manque {missing} émeraude{'s' if missing > 1 else ''}.")
    if not emeralds.credit(user, ref, -item.price, EmeraldEvent.Kind.PURCHASE, f"{item.name}"):
        raise ShopError(f"« {item.name} » a déjà été acheté.")


def _locked_profile(user):
    profile, _ = LearnerProfile.objects.get_or_create(user=user)
    return LearnerProfile.objects.select_for_update().get(pk=profile.pk)


@transaction.atomic
def buy_item(user, key, day=None):
    """
    Achète un objet. Les objets permanents sont équipés aussitôt ; les bonus
    prennent effet immédiatement (potion) ou sont mis en réserve (gel de série).
    La seconde révision est achetée depuis la page de révision (`day` : jour concerné).
    """
    item = items.get(key)
    if item is None:
        raise ShopError("Cet objet n'existe pas.")
    profile = _locked_profile(user)
    now = timezone.now()
    if item.category in items.COSMETICS:
        if key in owned_items(user):
            raise ShopError(f"Vous possédez déjà « {item.name} ».")
        _pay(user, item, f"achat:objet:{key}")
        ItemPurchase.objects.create(user=user, item=key, price=item.price)
        setattr(profile, PROFILE_FIELDS[item.category], key)
        profile.save(update_fields=[PROFILE_FIELDS[item.category]])
    elif key == items.STREAK_FREEZE:
        if profile.streak_freezes >= item.stock_max:
            raise ShopError(f"Vous avez déjà {item.stock_max} gels de série en réserve.")
        _pay(user, item, f"achat:gel:{now.isoformat()}")
        profile.streak_freezes += 1
        profile.save(update_fields=["streak_freezes"])
    elif key == items.DOUBLE_XP:
        if profile.double_xp_until and profile.double_xp_until > now:
            raise ShopError("Une potion double XP est déjà active.")
        _pay(user, item, f"achat:double-xp:{now.isoformat()}")
        profile.double_xp_until = now + timedelta(minutes=items.DOUBLE_XP_MINUTES)
        profile.save(update_fields=["double_xp_until"])
    elif key == items.SECOND_REVIEW:
        if day is None:
            raise ShopError("La seconde révision s'achète depuis la page de révision du jour.")
        _pay(user, item, f"achat:seconde-revision:{day.isoformat()}")
    else:
        raise ShopError("Cet objet ne peut pas être acheté.")
    return item


def has_second_review(user, day):
    return EmeraldEvent.objects.filter(
        user=user, ref=f"achat:seconde-revision:{day.isoformat()}"
    ).exists()


def equip_item(user, category, key):
    """Équipe un objet possédé (clé vide : aucun)."""
    if category not in PROFILE_FIELDS:
        raise ShopError("Catégorie inconnue.")
    if key and (items.get(key, category) is None or key not in owned_items(user)):
        raise ShopError("Vous ne possédez pas cet objet.")
    LearnerProfile.objects.update_or_create(
        user=user, defaults={PROFILE_FIELDS[category]: key or ""}
    )
