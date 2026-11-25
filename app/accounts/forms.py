from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignUpForm(UserCreationForm):
    """Création de compte : pseudo + mot de passe (e-mail facultatif)."""

    class Meta:
        model = User
        fields = ("username", "email")
        labels = {"username": "Pseudo", "email": "E-mail (facultatif)"}
        help_texts = {
            "username": "150 caractères maximum : lettres, chiffres et @ . + - _",
        }

    def clean_username(self):
        username = self.cleaned_data["username"]
        # Unicité insensible à la casse pour éviter "Alice" et "alice".
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ce pseudo est déjà utilisé.")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        # Un compte créé depuis le formulaire public n'a jamais de droits élevés.
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class NewUserForm(forms.Form):
    """Compte créé par un administrateur : la personne choisit elle-même son mot de passe (lien
    d'invitation)."""
    ROLE_CHOICES = [
        ("", "Apprenant"),
        ("redacteur", "Rédacteur"),
        ("gestionnaire", "Gestionnaire de contenu"),
    ]
    username = forms.RegexField(
        label="Pseudo",
        max_length=150,
        regex=r"^[\w.@+-]+\Z",
        error_messages={"invalid": "Lettres, chiffres et @ . + - _ uniquement."},
    )
    email = forms.EmailField(label="Adresse e-mail", required=False)
    role = forms.ChoiceField(label="Rôle", choices=ROLE_CHOICES, required=False)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ce pseudo est déjà utilisé.")
        return username
