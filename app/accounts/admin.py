from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from . import audit
from .models import AuditEvent, TwoFactor, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "is_active",
        "is_staff",
        "is_superuser",
        "last_login",
        "date_joined",
    )
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("username", "email")
    ordering = ("username",)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Journal en lecture seule : personne ne peut le modifier ni l'effacer depuis l'interface."""
    list_display = ("at", "username", "action", "target", "ip")
    list_filter = ("action",)
    search_fields = ("username", "target", "ip")
    date_hierarchy = "at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TwoFactor)
class TwoFactorAdmin(admin.ModelAdmin):
    """Supprimer la ligne réinitialise la double authentification (téléphone perdu, plus de codes de
    secours)."""
    list_display = ("user", "confirmed_at", "recovery_left")
    search_fields = ("user__username",)
    fields = ("user", "confirmed_at")
    readonly_fields = ("user", "confirmed_at")

    @admin.display(description="codes de secours restants")
    def recovery_left(self, obj):
        return len(obj.recovery_codes)

    def has_add_permission(self, request):
        return False

    def delete_model(self, request, obj):
        audit.record(request, "2fa_desactivee", target=obj.user.username, par_administrateur=True)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)
