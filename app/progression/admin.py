from django.contrib import admin

from .models import (
    AvatarPurchase,
    EmeraldEvent,
    ItemPurchase,
    LearnerProfile,
    StreakFreezeUse,
    XPEvent,
)

@admin.register(LearnerProfile)
class LearnerProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "daily_goal",
        "avatar",
        "frame",
        "theme",
        "profile_title",
        "streak_freezes",
    )
    search_fields = ("user__username",)


@admin.register(XPEvent)
class XPEventAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "kind", "label", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "label", "ref")
    readonly_fields = ("user", "amount", "kind", "label", "ref", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(EmeraldEvent)
class EmeraldEventAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "kind", "label", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "label", "ref")
    readonly_fields = ("user", "amount", "kind", "label", "ref", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(AvatarPurchase)
class AvatarPurchaseAdmin(admin.ModelAdmin):
    list_display = ("user", "avatar", "price", "purchased_at")
    search_fields = ("user__username", "avatar")
    readonly_fields = ("user", "avatar", "price", "purchased_at")

    def has_add_permission(self, request):
        return False


@admin.register(ItemPurchase)
class ItemPurchaseAdmin(admin.ModelAdmin):
    list_display = ("user", "item", "price", "purchased_at")
    search_fields = ("user__username", "item")
    readonly_fields = ("user", "item", "price", "purchased_at")

    def has_add_permission(self, request):
        return False


@admin.register(StreakFreezeUse)
class StreakFreezeUseAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "used_at")
    search_fields = ("user__username",)
    readonly_fields = ("user", "date", "used_at")

    def has_add_permission(self, request):
        return False
