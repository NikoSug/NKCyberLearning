"""
Progression façon « Duolingo » : points d'expérience (XP), niveaux, objectif
quotidien et série de jours consécutifs. Les règles de calcul sont dans xp.py.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class LearnerProfile(models.Model):
    """Préférences de progression de l'utilisateur (créées au premier besoin)."""

    class DailyGoal(models.IntegerChoices):
        RELAX = 10, "Détente — 10 XP par jour"
        REGULAR = 20, "Régulier — 20 XP par jour"
        SERIOUS = 50, "Sérieux — 50 XP par jour"
        INTENSE = 100, "Intensif — 100 XP par jour"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                                on_delete=models.CASCADE, related_name="learner_profile")
    daily_goal = models.PositiveSmallIntegerField("objectif quotidien", choices=DailyGoal.choices,
                                                  default=DailyGoal.REGULAR)
    avatar = models.CharField(
        "avatar choisi", max_length=40, blank=True
    )  # clé de progression/avatars.py
    # Objets de la boutique équipés (clés de progression/items.py) et bonus en cours.
    frame = models.CharField("cadre d'avatar", max_length=40, blank=True)
    theme = models.CharField("thème de l'interface", max_length=40, blank=True)
    profile_title = models.CharField("titre de profil", max_length=40, blank=True)
    streak_freezes = models.PositiveSmallIntegerField("gels de série en réserve", default=0)
    double_xp_until = models.DateTimeField("double XP jusqu'à", null=True, blank=True)

    class Meta:
        verbose_name = "profil de progression"
        verbose_name_plural = "profils de progression"

    def __str__(self):
        return f"{self.user} — {self.daily_goal} XP/jour"


class XPEvent(models.Model):
    """
    Un gain d'XP. La référence `ref` est unique par utilisateur : une même action
    (finir un guide, réussir une question…) ne rapporte jamais deux fois.
    """

    class Kind(models.TextChoices):
        READING = "lecture", "Lecture"
        TEST = "test", "Test de connaissances"
        REVIEW = "revision", "Révision du jour"
        LESSON = "lecon", "Leçon"
        BONUS = "bonus", "Bonus"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="xp_events")
    amount = models.PositiveSmallIntegerField("XP")
    kind = models.CharField("type", max_length=12, choices=Kind.choices)
    label = models.CharField("libellé", max_length=200)
    ref = models.CharField("référence", max_length=120)
    created_at = models.DateTimeField("gagnés le", default=timezone.now)

    class Meta:
        verbose_name = "gain d'XP"
        verbose_name_plural = "gains d'XP"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "ref"], name="xp_une_fois_par_action")
        ]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user} +{self.amount} XP — {self.label}"


class EmeraldEvent(models.Model):
    """
    Mouvement d'émeraudes, la monnaie de la boutique : gain (montant positif) ou
    achat (montant négatif). Le solde est la somme des mouvements. Comme pour
    l'XP, la référence `ref` est unique : une même récompense n'est versée qu'une fois.
    """

    class Kind(models.TextChoices):
        LEVEL = "niveau", "Niveau atteint"
        GOAL = "objectif", "Objectif du jour"
        STREAK = "serie", "Série de jours"
        PERFECT = "parfait", "Sans faute"
        COMPLETION = "complet", "Test terminé"
        PURCHASE = "achat", "Achat"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="emerald_events")
    amount = models.IntegerField("émeraudes")
    kind = models.CharField("type", max_length=10, choices=Kind.choices)
    label = models.CharField("libellé", max_length=200)
    ref = models.CharField("référence", max_length=120)
    created_at = models.DateTimeField("le", default=timezone.now)

    class Meta:
        verbose_name = "mouvement d'émeraudes"
        verbose_name_plural = "mouvements d'émeraudes"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "ref"], name="emeraude_une_fois_par_action")
        ]

    def __str__(self):
        return f"{self.user} {self.amount:+d} émeraudes — {self.label}"


class AvatarPurchase(models.Model):
    """Avatar acheté avec des émeraudes. Le prix payé est conservé (le catalogue peut évoluer)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="avatar_purchases")
    avatar = models.CharField("avatar", max_length=40)
    price = models.PositiveIntegerField("prix payé (émeraudes)")
    purchased_at = models.DateTimeField("acheté le", default=timezone.now)

    class Meta:
        verbose_name = "achat d'avatar"
        verbose_name_plural = "achats d'avatars"
        ordering = ["-purchased_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "avatar"], name="avatar_achete_une_fois")
        ]

    def __str__(self):
        return f"{self.user} — {self.avatar} ({self.price} émeraudes)"


class ItemPurchase(models.Model):
    """Objet permanent acheté en émeraudes (cadre, thème, titre). Les bonus consommables n'ont pas
    de ligne ici."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="item_purchases")
    item = models.CharField("objet", max_length=40)
    price = models.PositiveIntegerField("prix payé (émeraudes)")
    purchased_at = models.DateTimeField("acheté le", default=timezone.now)

    class Meta:
        verbose_name = "achat d'objet"
        verbose_name_plural = "achats d'objets"
        ordering = ["-purchased_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "item"], name="objet_achete_une_fois")
        ]

    def __str__(self):
        return f"{self.user} — {self.item} ({self.price} émeraudes)"


class StreakFreezeUse(models.Model):
    """Jour manqué couvert par un gel de série : la série continue sans augmenter ce jour-là."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="streak_freezes_used")
    date = models.DateField("jour protégé")
    used_at = models.DateTimeField("utilisé le", default=timezone.now)

    class Meta:
        verbose_name = "gel de série utilisé"
        verbose_name_plural = "gels de série utilisés"
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="un_gel_par_jour")]

    def __str__(self):
        return f"{self.user} — gel du {self.date:%d/%m/%Y}"


class Vacation(models.Model):
    """
    Période de vacances déclarée par l'utilisateur (Sécurité et données → Mode vacances) : aucune
    lecture n'est
    planifiée, la série de jours ne peut pas se casser et aucun gel n'est consommé. Les périodes
    passées restent
    enregistrées pour que l'historique de la série reste juste.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="vacations")
    start = models.DateField("du")
    end = models.DateField("au (inclus)")
    created_at = models.DateTimeField("déclarée le", default=timezone.now)

    class Meta:
        verbose_name = "période de vacances"
        verbose_name_plural = "périodes de vacances"
        ordering = ["-start"]

    def __str__(self):
        return f"{self.user} — vacances du {self.start:%d/%m/%Y} au {self.end:%d/%m/%Y}"
