"""
Amis : une invitation envoyée par un utilisateur, acceptée (ou non) par l'autre.

Seuls des amis acceptés voient la progression l'un de l'autre (niveau, série, XP
de la semaine). Une invitation refusée est simplement supprimée.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Friendship(models.Model):
    class Status(models.TextChoices):
        PENDING = "attente", "En attente"
        ACCEPTED = "acceptee", "Acceptée"

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="demandeur",
                                  on_delete=models.CASCADE, related_name="friend_requests_sent")
    addressee = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="destinataire",
                                  on_delete=models.CASCADE, related_name="friend_requests_received")
    status = models.CharField(
        "statut", max_length=10, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField("envoyée le", default=timezone.now)
    accepted_at = models.DateTimeField("acceptée le", null=True, blank=True)

    class Meta:
        verbose_name = "amitié"
        verbose_name_plural = "amitiés"
        constraints = [
            models.UniqueConstraint(
                fields=["requester", "addressee"], name="une_invitation_par_paire"
            ),
            models.CheckConstraint(
                condition=~Q(requester=F("addressee")), name="pas_ami_avec_soi_meme"
            ),
        ]

    def __str__(self):
        return f"{self.requester} → {self.addressee} ({self.get_status_display()})"
