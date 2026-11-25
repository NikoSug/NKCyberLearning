from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import avatars, emeralds, items, xp
from . import shop as shop_rules
from .models import LearnerProfile, XPEvent

HEATMAP_WEEKS = 12


def _heat(amount, goal):
    """Intensité 0 à 4 d'une case du calendrier, relative à l'objectif quotidien."""
    if not amount:
        return 0
    return min(4, 1 + (4 * amount) // max(goal * 2, 1))


@login_required
def overview(request):
    """Ma progression : niveau, objectif du jour, série, calendrier et historique des gains."""
    summary = xp.summary(request.user)
    request._xp_summary = summary
    today = timezone.localdate()
    totals = xp.daily_totals(request.user, days=HEATMAP_WEEKS * 7)
    # Calendrier : colonnes = semaines (lundi en haut), de la plus ancienne à la courante.
    start = today - timedelta(days=today.weekday() + 7 * (HEATMAP_WEEKS - 1))
    weeks = []
    for w in range(HEATMAP_WEEKS):
        week = []
        for d in range(7):
            day = start + timedelta(days=7 * w + d)
            amount = totals.get(day, 0)
            week.append(
                {
                    "date": day,
                    "xp": amount,
                    "heat": _heat(amount, summary.goal),
                    "future": day > today,
                }
            )
        weeks.append(week)
    best_week_day = max((amount for _, amount in summary.week), default=0)
    levels = [
        {
            "level": n,
            "title": xp.level_title(n),
            "start": xp.level_start(n),
            "emeralds": emeralds.level_reward(n),
            "unlocks": [r for r in avatars.RARITIES if r.min_level == n and r.price],
        }
        for n in range(max(1, summary.level - 2), summary.level + 4)
    ]
    return render(
        request,
        "progression/overview.html",
        {
            "summary": summary,
            "weeks": weeks,
            "week_bars": [
                {
                    "date": day,
                    "xp": amount,
                    "height": round(10 * amount / best_week_day) if best_week_day else 0,
                }
                for day, amount in summary.week
            ],
            "events": XPEvent.objects.filter(user=request.user)[:25],
            "levels": levels,
            "goals": LearnerProfile.DailyGoal.choices,
            "streak_milestones": emeralds.STREAK_MILESTONES.items(),
            "emerald_rules": emeralds,
            "active_days": sum(1 for amount in totals.values() if amount),
        },
    )


@login_required
@require_POST
def set_goal(request):
    try:
        goal = int(request.POST.get("daily_goal", ""))
    except ValueError:
        goal = None
    if goal in LearnerProfile.DailyGoal.values:
        profile = xp.profile(request.user)
        profile.daily_goal = goal
        profile.save(update_fields=["daily_goal"])
        messages.success(request, f"Objectif quotidien : {goal} XP par jour.")
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "progression:overview"
    return redirect(target)


@login_required
def shop(request):
    """Boutique d'avatars : achat avec les XP disponibles et choix de l'avatar affiché."""
    summary = xp.summary(request.user)
    request._xp_summary = summary
    owned = shop_rules.owned(request.user)
    groups = []
    for rarity in avatars.RARITIES:
        entries = [{
            "avatar": avatar, "owned": avatar.key in owned,
            "equipped": summary.avatar is not None and summary.avatar.key == avatar.key,
            "locked": summary.level < avatar.min_level,
            "affordable": summary.emeralds >= avatar.price,
        } for avatar in avatars.AVATARS if avatar.rarity == rarity]
        if entries:
            groups.append({"rarity": rarity, "items": entries})
    owned_items = shop_rules.owned_items(request.user)
    equipped = {
        items.FRAME: summary.frame,
        items.THEME: summary.theme,
        items.TITLE: summary.profile_title,
    }
    sections = []
    for category, label, help_text in items.CATEGORIES:
        sections.append({"category": category, "label": label, "help": help_text, "items": [{
            "item": item, "owned": item.key in owned_items,
            "equipped": equipped.get(category) is not None and equipped[category].key == item.key,
            "locked": summary.level < item.min_level, "affordable": summary.emeralds >= item.price,
        } for item in items.of(category)]})
    return render(
        request,
        "progression/shop.html",
        {
            "summary": summary,
            "groups": groups,
            "sections": sections,
            "history": emeralds.history(request.user, limit=12),
            "second_review_item": items.SECOND_REVIEW,
            "freeze_item": items.STREAK_FREEZE,
            "double_item": items.DOUBLE_XP,
        },
    )


@login_required
@require_POST
def buy_avatar(request):
    try:
        avatar = shop_rules.buy(request.user, request.POST.get("avatar", ""))
    except shop_rules.ShopError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"« {avatar.name} » est à vous ! C'est désormais votre avatar.")
    return redirect("progression:shop")


@login_required
@require_POST
def equip_avatar(request):
    try:
        shop_rules.equip(request.user, request.POST.get("avatar", ""))
    except shop_rules.ShopError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Avatar mis à jour.")
    return redirect("progression:shop")


def _back_to_shop(section):
    """Retour à la boutique, sur la section de l'objet (ancre limitée aux catégories connues)."""
    anchor = section if section in {category for category, _, _ in items.CATEGORIES} else ""
    return redirect(reverse("progression:shop") + (f"#{anchor}" if anchor else ""))


@login_required
@require_POST
def buy_item(request):
    try:
        item = shop_rules.buy_item(request.user, request.POST.get("item", ""))
    except shop_rules.ShopError as exc:
        messages.error(request, str(exc))
    else:
        if item.key == items.DOUBLE_XP:
            messages.success(
                request,
                f"Potion bue : vos gains d'XP sont doublés pendant {items.DOUBLE_XP_MINUTES}"
                " minutes !",
            )
        elif item.key == items.STREAK_FREEZE:
            messages.success(
                request,
                "Gel de série ajouté à votre réserve : il protégera votre série un jour d'absence.",
            )
        else:
            messages.success(request, f"« {item.name} » est à vous et déjà équipé !")
    return _back_to_shop(request.POST.get("section"))


@login_required
@require_POST
def equip_item(request):
    try:
        shop_rules.equip_item(
            request.user, request.POST.get("category", ""), request.POST.get("item", "")
        )
    except shop_rules.ShopError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "C'est noté.")
    return _back_to_shop(request.POST.get("category"))
