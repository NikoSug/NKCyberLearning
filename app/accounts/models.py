from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver


class User(AbstractUser):
    """
    Utilisateur de la plateforme.

    La connexion se fait par pseudo (champ `username` hérité, unique) et mot de
    passe. Un modèle personnalisé est déclaré dès le départ : c'est la pratique
    recommandée par Django, car il est très difficile d'en changer après la
    première migration.

    Droits :
      - utilisateur standard : lit les guides, consulte les certifications,
        marque ses lectures ;
      - rédacteur (groupe "Rédacteurs") : écrit des cours et des podcasts dans
        l'atelier de création, avec l'aide de l'IA ; ils sont relus avant publication ;
      - gestionnaire de contenu (groupe "Gestionnaires de contenu", accès à
        l'administration limité au catalogue) : relit, publie, rédige aussi ;
      - administrateur (is_superuser) : tous les droits, y compris la gestion
        des comptes.
    """

    email = models.EmailField("adresse e-mail", blank=True)

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"

    @property
    def group_names(self):
        """Noms des groupes, lus une seule fois par requête (vidé quand les groupes changent, voir
        plus bas)."""
        if "_group_names" not in self.__dict__:
            prefetched = getattr(self, "_prefetched_objects_cache", {}).get("groups")
            groups = prefetched if prefetched is not None else self.groups.all()
            self.__dict__["_group_names"] = frozenset(group.name for group in groups)
        return self.__dict__["_group_names"]

    @property
    def is_content_manager(self):
        return self.is_superuser or CONTENT_MANAGERS_GROUP in self.group_names

    @property
    def is_author(self):
        """Peut écrire dans l'atelier de création (les gestionnaires de contenu aussi)."""
        return self.is_superuser or bool(self.group_names & {AUTHORS_GROUP, CONTENT_MANAGERS_GROUP})

    @property
    def is_privileged(self):
        """Compte à droits élevés : double authentification et déconnexion après inactivité."""
        return self.is_staff or self.is_superuser or self.is_author

    def refresh_from_db(self, *args, **kwargs):
        self.__dict__.pop("_group_names", None)
        super().refresh_from_db(*args, **kwargs)


CONTENT_MANAGERS_GROUP = "Gestionnaires de contenu"
AUTHORS_GROUP = "Rédacteurs"


@receiver(m2m_changed, sender=User.groups.through)
def _forget_group_names(sender, instance, **kwargs):
    """Rôle donné ou retiré : l'objet modifié relira ses groupes."""
    if isinstance(instance, User):
        instance.__dict__.pop("_group_names", None)


class TwoFactor(models.Model):
    """
    Double authentification d'un compte : secret TOTP partagé avec l'application du téléphone et
    codes
    de secours (enregistrés sous forme d'empreintes, comme les mots de passe).
    """

    user = models.OneToOneField(
        User, verbose_name="utilisateur", on_delete=models.CASCADE, related_name="two_factor"
    )
    secret = models.CharField("secret TOTP", max_length=64)
    confirmed_at = models.DateTimeField("activée le", null=True, blank=True)
    last_counter = models.BigIntegerField(
        "dernier code utilisé", default=-1
    )  # un code ne sert qu'une fois
    recovery_codes = models.JSONField("codes de secours (empreintes)", default=list, blank=True)

    class Meta:
        verbose_name = "double authentification"
        verbose_name_plural = "doubles authentifications"

    def __str__(self):
        return f"{self.user} ({'active' if self.confirmed_at else 'en cours'})"


class AuditEvent(models.Model):
    """Journal des actions sensibles : connexions, rôles, double authentification, publications,
    suppressions."""

    at = models.DateTimeField("date", auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        User,
        verbose_name="auteur",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    username = models.CharField(
        "pseudo", max_length=150, blank=True
    )  # gardé si le compte est supprimé
    action = models.CharField("action", max_length=40, db_index=True)
    target = models.CharField("objet", max_length=255, blank=True)
    detail = models.JSONField("détails", default=dict, blank=True)
    ip = models.GenericIPAddressField("adresse IP", null=True, blank=True)

    class Meta:
        verbose_name = "événement du journal"
        verbose_name_plural = "journal d'audit"
        ordering = ["-at"]

    def __str__(self):
        return f"{self.at:%d/%m/%Y %H:%M} {self.username or '—'} {self.action} {self.target}"
