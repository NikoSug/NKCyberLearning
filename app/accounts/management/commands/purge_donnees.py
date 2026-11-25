"""
Durées de conservation (RGPD) : supprime ce qui n'est plus utile.
  - journal d'audit : AUDIT_RETENTION_DAYS jours (365 par défaut) ;
  - traces des questions à l'assistant (sans leur texte) et tentatives de connexion (django-axes) :
  90 jours ;
  - sessions expirées.
Lancée à chaque démarrage du service web ; à planifier aussi chaque nuit (voir README).
"""

from datetime import timedelta

from axes.models import AccessAttempt, AccessFailureLog, AccessLog
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AuditEvent
from catalogue.models import AssistantQuestion

SHORT_DAYS = 90


class Command(BaseCommand):
    help = "Supprime les données personnelles arrivées au terme de leur durée de conservation."

    def handle(self, *args, **options):
        now = timezone.now()
        short = now - timedelta(days=SHORT_DAYS)
        removed = {
            "journal d'audit": AuditEvent.objects.filter(
                at__lt=now - timedelta(days=settings.AUDIT_RETENTION_DAYS)
            ),
            "questions à l'assistant": AssistantQuestion.objects.filter(asked_at__lt=short),
            "tentatives de connexion": AccessAttempt.objects.filter(attempt_time__lt=short),
            "échecs de connexion": AccessFailureLog.objects.filter(attempt_time__lt=short),
            "historique des connexions": AccessLog.objects.filter(attempt_time__lt=short),
            "sessions expirées": Session.objects.filter(expire_date__lt=now),
        }
        for label, queryset in removed.items():
            count = queryset.delete()[0]
            if count:
                self.stdout.write(f"{label} : {count} supprimé(s)")
        self.stdout.write(self.style.SUCCESS("Purge terminée."))
