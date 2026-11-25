from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from . import audit, invitations
from .forms import NewUserForm, SignUpForm
from .models import AUTHORS_GROUP, CONTENT_MANAGERS_GROUP
from .ratelimit import rate_limit

ROLES = {
    "redacteur": (AUTHORS_GROUP, "Rédacteur"),
    "gestionnaire": (CONTENT_MANAGERS_GROUP, "Gestionnaire de contenu"),
}
ROLE_FILTERS = [
    ("", "Tous"),
    ("redacteur", "Rédacteurs"),
    ("gestionnaire", "Gestionnaires"),
    ("admin", "Administrateurs"),
    ("inactif", "Désactivés"),
]


@require_http_methods(["GET", "POST"])
@rate_limit("inscription", 5, 3600, methods=("POST",))
def signup(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404("Les inscriptions sont fermées.")
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Bienvenue ! Votre compte a été créé.")
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/signup.html", {"form": form})


# --- Gestion des utilisateurs (administrateurs) ----------------------------------------------
def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Réservé aux administrateurs.")
        return view(request, *args, **kwargs)
    return wrapper


@admin_required
def user_list(request):
    """Comptes et rôles : rédacteur (atelier de création), gestionnaire de contenu (relecture,
    publication)."""
    query, role = request.GET.get("q", "").strip()[:60], request.GET.get("role", "")
    users = (
        get_user_model().objects.prefetch_related("groups").order_by("-is_superuser", "username")
    )
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query))
    if role in ROLES:
        users = users.filter(groups__name=ROLES[role][0])
    elif role == "admin":
        users = users.filter(is_superuser=True)
    elif role == "inactif":
        users = users.filter(is_active=False)
    page = Paginator(users.distinct(), 50).get_page(request.GET.get("page"))
    for user in page:
        names = {group.name for group in user.groups.all()}
        user.roles = {key: group in names for key, (group, _label) in ROLES.items()}
    return render(
        request,
        "accounts/user_list.html",
        {
            "page": page,
            "query": query,
            "role": role,
            "role_filters": ROLE_FILTERS,
            "new_form": NewUserForm(),
            "counts": {
                key: get_user_model().objects.filter(groups__name=group).count()
                for key, (group, _l) in ROLES.items()
            },
        },
    )


@admin_required
@require_POST
def user_create(request):
    """Nouveau compte, sans mot de passe : un lien d'invitation (e-mail, ou à transmettre) permet de
    le choisir."""
    form = NewUserForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
        return redirect("accounts:user_list")
    user = get_user_model().objects.create_user(
        form.cleaned_data["username"], email=form.cleaned_data["email"]
    )
    role = form.cleaned_data["role"]
    if role in ROLES:
        user.groups.add(Group.objects.get_or_create(name=ROLES[role][0])[0])
        if role == "gestionnaire":
            user.is_staff = True
            user.save(update_fields=["is_staff"])
    audit.record(
        request,
        "compte_cree",
        target=user.username,
        role=ROLES[role][1] if role in ROLES else "Apprenant",
    )
    return _link_page(request, user, new=True)


def _link_page(request, user, new):
    link = invitations.set_password_link(request, user)
    sent = invitations.send_link(user, link, new)
    audit.record(request, "lien_mot_de_passe", target=user.username, envoye_par_email=sent)
    response = render(request, "accounts/invitation_link.html", {
        "target": user, "link": link, "sent": sent, "new": new,
        "days": settings.PASSWORD_RESET_TIMEOUT // 86400})
    response["Cache-Control"] = "no-store"  # le lien vaut un mot de passe pendant sa validité
    return response


@admin_required
@require_POST
def user_action(request, pk):
    """Donner ou retirer un rôle, désactiver ou réactiver un compte."""
    user = get_object_or_404(get_user_model(), pk=pk)
    action, back = request.POST.get("action", ""), request.POST.get("next", "")
    target = (
        back if back.startswith(reverse("accounts:user_list")) else reverse("accounts:user_list")
    )
    if user == request.user or user.is_superuser:
        messages.error(
            request,
            "Les comptes administrateurs se gèrent dans l'administration (pas depuis cette page).",
        )
        return redirect(target)
    verb, _, key = action.partition("-")
    if verb in ("donner", "retirer") and key in ROLES:
        group_name, label = ROLES[key]
        group = Group.objects.get_or_create(name=group_name)[0]
        if verb == "donner":
            user.groups.add(group)
        else:
            user.groups.remove(group)
        if key == "gestionnaire":  # l'administration du catalogue demande le statut « équipe »
            user.is_staff = verb == "donner"
            user.save(update_fields=["is_staff"])
        audit.record(
            request,
            "role_donne" if verb == "donner" else "role_retire",
            target=user.username,
            role=label,
        )
        messages.success(
            request,
            f"{user.username} : rôle « {label} » {'donné' if verb == 'donner' else 'retiré'}.",
        )
    elif action == "lien":  # mot de passe perdu, invitation expirée
        return _link_page(request, user, new=user.last_login is None)
    elif action in ("desactiver", "reactiver"):
        user.is_active = action == "reactiver"
        user.save(update_fields=["is_active"])
        audit.record(
            request,
            "compte_reactive" if user.is_active else "compte_desactive",
            target=user.username,
        )
        messages.success(
            request, f"Compte de {user.username} {'réactivé' if user.is_active else 'désactivé'}."
        )
    else:
        messages.error(request, "Action inconnue.")
    return redirect(target)
