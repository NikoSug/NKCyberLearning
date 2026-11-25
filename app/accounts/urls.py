from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import security_views, views
from .ratelimit import rate_limit

app_name = "accounts"

urlpatterns = [
    path(
        "connexion/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            redirect_authenticated_user=True,
            extra_context={"signup_enabled": settings.SIGNUP_ENABLED},
        ),
        name="login",
    ),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="logout"),  # POST uniquement
    path("inscription/", views.signup, name="signup"),
    path(
        "mot-de-passe/",
        security_views.PasswordChangeView.as_view(
            template_name="accounts/password_change.html",
            success_url=reverse_lazy("accounts:password_change_done"),
        ),
        name="password_change",
    ),
    path(
        "mot-de-passe/ok/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html"
        ),
        name="password_change_done",
    ),
    # Sécurité du compte : double authentification, données personnelles (RGPD)
    path("securite/", security_views.security, name="security"),
    path("securite/vacances/", security_views.vacation, name="vacation"),
    path("double-authentification/", security_views.two_factor_setup, name="two_factor_setup"),
    path(
        "double-authentification/code/", security_views.two_factor_verify, name="two_factor_verify"
    ),
    path(
        "double-authentification/codes-de-secours/",
        security_views.two_factor_codes,
        name="two_factor_codes",
    ),
    path(
        "double-authentification/desactiver/",
        security_views.two_factor_disable,
        name="two_factor_disable",
    ),
    path("mes-donnees/", security_views.export_data, name="export_data"),
    path("supprimer/", security_views.delete_account, name="delete_account"),
    # Mot de passe oublié (si les e-mails sont configurés) et liens d'invitation
    path(
        "mot-de-passe-oublie/",
        rate_limit("reinitialisation", 5, 3600, methods=("POST",))(
            auth_views.PasswordResetView.as_view(
                template_name="accounts/password_reset_form.html",
                email_template_name="accounts/emails/password_reset.txt",
                subject_template_name="accounts/emails/password_reset_subject.txt",
                extra_email_context={"instance_name": settings.INSTANCE_NAME},
                success_url=reverse_lazy("accounts:password_reset_done"),
            )
        ),
        name="password_reset",
    ),
    path(
        "mot-de-passe-oublie/envoye/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "nouveau-mot-de-passe/<uidb64>/<token>/",
        security_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "nouveau-mot-de-passe/termine/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("utilisateurs/nouveau/", views.user_create, name="user_create"),
    path("utilisateurs/", views.user_list, name="user_list"),
    path("utilisateurs/<int:pk>/", views.user_action, name="user_action"),
]
