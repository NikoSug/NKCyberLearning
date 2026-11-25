"""
Sécurité du compte : double authentification (activation, vérification à la connexion, codes de
secours),
export des données personnelles et suppression du compte (RGPD).
"""

import json
from datetime import timedelta

import segno
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core import serializers
from django.db import models, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from progression import vacations

from . import audit, totp, two_factor
from .ratelimit import hit
from .models import AuditEvent, TwoFactor

MAX_FAILURES = 5
# Par compte, quelle que soit la session : se reconnecter ne redonne pas d'essais (force brute du
# code).
MAX_ATTEMPTS, ATTEMPTS_PERIOD = 10, 900
FAILURES_KEY = "2fa_echecs"


class CodeForm(forms.Form):
    code = forms.CharField(label="Code", max_length=20, widget=forms.TextInput(attrs={
        "autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}))


class PasswordCodeForm(CodeForm):
    password = forms.CharField(
        label="Mot de passe", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )


class VacationForm(forms.Form):
    end = forms.DateField(
        label="Je suis en vacances jusqu'au (inclus)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean_end(self):
        end, today = self.cleaned_data["end"], timezone.localdate()
        if end < today:
            raise forms.ValidationError("Choisissez une date à venir.")
        if end > today + timedelta(days=vacations.MAX_DAYS):
            raise forms.ValidationError(
                f"{vacations.MAX_DAYS} jours au plus : vous pourrez prolonger à votre retour."
            )
        return end


class DeleteForm(forms.Form):
    password = forms.CharField(
        label="Mot de passe", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )
    confirm = forms.CharField(label="Votre pseudo, pour confirmer", max_length=150)


def _next(request):
    target = request.POST.get("next") or request.GET.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, {request.get_host()}, request.is_secure()
    ):
        return target
    return reverse(settings.LOGIN_REDIRECT_URL)


def _qr(uri):
    # Image SVG en « data: » (autorisée par la CSP) : pas de HTML injecté dans la page.
    return segno.make(uri, error="m").svg_data_uri(
        scale=5, border=2, dark="#111827", light="#ffffff"
    )


@login_required
@require_http_methods(["GET", "POST"])
def two_factor_setup(request):
    """Activation : scanner le QR code, saisir un premier code, noter les codes de secours."""
    if two_factor.device(request.user):
        return redirect("accounts:security")
    item = two_factor.pending(request.user)
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        codes = two_factor.confirm(item, form.cleaned_data["code"])
        if codes:
            two_factor.mark_verified(request)
            audit.record(request, "2fa_activee")
            return render(
                request,
                "accounts/recovery_codes.html",
                {"codes": codes, "next": _next(request), "new": True},
            )
        form.add_error(
            "code", "Code incorrect : vérifiez l'heure du téléphone et saisissez le code affiché."
        )
    return render(
        request,
        "accounts/two_factor_setup.html",
        {
            "form": form,
            "qr": _qr(
                totp.provisioning_uri(
                    item.secret, request.user.username, issuer=settings.INSTANCE_NAME
                )
            ),
            "secret": " ".join(item.secret[i : i + 4] for i in range(0, len(item.secret), 4)),
            "required": request.user.is_privileged and settings.MFA_REQUIRED_FOR_PRIVILEGED,
            "next": request.GET.get("next", ""),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def two_factor_verify(request):
    """Second facteur, une fois par session."""
    if not two_factor.device(request.user):
        return redirect("accounts:two_factor_setup")
    if two_factor.verified(request):
        return redirect(_next(request))
    if request.method == "POST" and not hit(
        "2fa", f"u{request.user.pk}", MAX_ATTEMPTS, ATTEMPTS_PERIOD
    ):
        audit.record(request, "2fa_echec", bloque=True)
        logout(request)
        messages.error(request, "Trop de codes essayés : réessayez dans un quart d'heure.")
        return redirect(settings.LOGIN_URL)
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        kind = two_factor.check(request.user, form.cleaned_data["code"])
        if kind:
            request.session.pop(FAILURES_KEY, None)
            two_factor.mark_verified(request)
            if kind == "secours":
                left = two_factor.remaining_recovery(request.user)
                audit.record(request, "2fa_secours", restants=left)
                messages.warning(request, f"Code de secours utilisé : il vous en reste {left}. "
                                          "Régénérez-les depuis la page Sécurité si besoin.")
            return redirect(_next(request))
        failures = request.session.get(FAILURES_KEY, 0) + 1
        audit.record(request, "2fa_echec", tentative=failures)
        if failures >= MAX_FAILURES:
            logout(request)
            messages.error(request, "Trop de codes incorrects : reconnectez-vous.")
            return redirect(settings.LOGIN_URL)
        request.session[FAILURES_KEY] = failures
        form.add_error("code", "Code incorrect.")
    return render(
        request,
        "accounts/two_factor_verify.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@login_required
def security(request):
    """Page « Sécurité » du compte."""
    item = two_factor.device(request.user)
    # Dernières connexions et tentatives (un échec de mot de passe n'est rattaché qu'au pseudo
    # tenté).
    events = list(
        AuditEvent.objects.filter(
            Q(user=request.user) | Q(user__isnull=True, username=request.user.username),
            action__in=(
                "connexion",
                "connexion_echec",
                "2fa_echec",
                "2fa_activee",
                "2fa_desactivee",
                "mot_de_passe",
            ),
        )[:10]
    )
    for event in events:
        event.label = audit.LABELS.get(event.action, event.action)
    return render(request, "accounts/security.html", {
        "device": item, "recovery_left": len(item.recovery_codes) if item else 0,
        "required": request.user.is_privileged and settings.MFA_REQUIRED_FOR_PRIVILEGED,
        "code_form": CodeForm(), "disable_form": PasswordCodeForm(), "delete_form": DeleteForm(),
        "vacation": vacations.current(request.user), "vacation_form": VacationForm(),
        "vacation_max": timezone.localdate() + timedelta(days=vacations.MAX_DAYS),
        "can_delete": not request.user.is_privileged, "events": events,
    })


@login_required
@require_POST
def vacation(request):
    """Mode vacances : aucune lecture planifiée, série protégée, jusqu'à la date choisie ou au
    retour."""
    if request.POST.get("action") == "retour":
        vacations.stop(request.user)
        messages.success(request, "Bon retour ! Votre planning de lecture reprend aujourd'hui.")
        return redirect(reverse("accounts:security") + "#vacances")
    form = VacationForm(request.POST)
    if form.is_valid():
        end = form.cleaned_data["end"]
        vacations.start(request.user, end)
        messages.success(
            request,
            f"Mode vacances activé jusqu'au {end:%d/%m/%Y} : pas de lecture prévue, "
            "votre série est protégée. Bonnes vacances !",
        )
    else:
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    return redirect(reverse("accounts:security") + "#vacances")


@login_required
@require_POST
def two_factor_codes(request):
    """Nouveaux codes de secours (les anciens ne marchent plus) ; demande un code de
    l'application."""
    form = CodeForm(request.POST)
    if not form.is_valid() or two_factor.check(request.user, form.cleaned_data["code"]) != "totp":
        messages.error(request, "Code de l'application incorrect.")
        return redirect("accounts:security")
    codes = two_factor.regenerate(two_factor.device(request.user))
    audit.record(request, "2fa_codes_regeneres")
    return render(
        request,
        "accounts/recovery_codes.html",
        {"codes": codes, "next": reverse("accounts:security")},
    )


@login_required
@require_POST
def two_factor_disable(request):
    if request.user.is_privileged and settings.MFA_REQUIRED_FOR_PRIVILEGED:
        messages.error(request, "La double authentification est obligatoire pour votre rôle.")
        return redirect("accounts:security")
    form = PasswordCodeForm(request.POST)
    if not form.is_valid() or not request.user.check_password(form.cleaned_data["password"]) \
            or not two_factor.check(request.user, form.cleaned_data["code"]):
        messages.error(request, "Mot de passe ou code incorrect.")
        return redirect("accounts:security")
    TwoFactor.objects.filter(user=request.user).delete()
    audit.record(request, "2fa_desactivee")
    messages.success(request, "Double authentification désactivée.")
    return redirect("accounts:security")


class PasswordChangeView(auth_views.PasswordChangeView):
    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(self.request, "mot_de_passe")
        return response


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Choix du mot de passe depuis un lien (invitation ou mot de passe oublié)."""
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(self.request, "mot_de_passe", user=form.user, par_lien=True)
        return response


# --- Données personnelles (RGPD) ------------------------------------------------------------------
# Champs jamais exportés : secrets et empreintes.
PRIVATE_FIELDS = {"password", "secret", "recovery_codes"}


def _personal_data(user):
    """Le compte et tout ce qui lui appartient (objets supprimés avec lui), au format JSON de
    Django."""
    data = {
        "compte": {
            "pseudo": user.username,
            "e-mail": user.email,
            "inscrit le": user.date_joined.isoformat(),
            "dernière connexion": user.last_login.isoformat() if user.last_login else None,
            "groupes": sorted(user.group_names),
        }
    }
    for relation in user._meta.related_objects:
        if relation.on_delete is not models.CASCADE or relation.related_model is AuditEvent:
            continue
        model = relation.related_model
        queryset = model.objects.filter(**{relation.field.name: user})
        if not queryset.exists():
            continue
        fields = [f.name for f in model._meta.concrete_fields if f.name not in PRIVATE_FIELDS]
        rows = json.loads(serializers.serialize("json", queryset, fields=fields))
        data[f"{model._meta.app_label}.{model._meta.model_name}"] = [
            row["fields"] | {"id": row["pk"]} for row in rows
        ]
    data["journal"] = [{"date": e.at.isoformat(), "action": e.action, "ip": e.ip}
                       for e in AuditEvent.objects.filter(user=user)[:500]]
    return data


@login_required
@require_POST
def export_data(request):
    payload = json.dumps(_personal_data(request.user), ensure_ascii=False, indent=2, default=str)
    audit.record(request, "donnees_exportees")
    response = HttpResponse(payload, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(
        True, f"cyberlearning-{request.user.username}-{timezone.localdate():%Y%m%d}.json")
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def delete_account(request):
    user = request.user
    if user.is_privileged:
        messages.error(request, "Un compte à droits élevés est supprimé par un administrateur.")
        return redirect("accounts:security")
    form = DeleteForm(request.POST)
    if not form.is_valid() or form.cleaned_data["confirm"] != user.username \
            or not user.check_password(form.cleaned_data["password"]):
        messages.error(
            request, "Mot de passe ou pseudo de confirmation incorrect : rien n'a été supprimé."
        )
        return redirect("accounts:security")
    with transaction.atomic():
        audit.record(request, "compte_supprime", username=user.username)
        logout(request)
        user.delete()
    messages.success(request, "Votre compte et vos données ont été supprimés.")
    return redirect(settings.LOGIN_URL)
