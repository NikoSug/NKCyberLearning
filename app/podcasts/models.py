"""
Podcasts générés par l'IA locale, inspirés des guides de l'ANSSI : une série par thème,
quelques épisodes où deux voix discutent du sujet.

  - Clara (première voix) anime : elle découvre le sujet et pose les questions de l'auditeur ;
  - Hugo (seconde voix) est l'expert : il explique en s'appuyant sur les guides.

Le script de chaque épisode est relu (et corrigé) avant l'enregistrement de l'audio ;
rien n'est visible des apprenants avant la publication.
"""
from django.conf import settings
from django.db import models
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from catalogue.models import GuideCategory, Level

HOSTS = (
    "Clara",
    "Hugo",
)  # indice = voix du service de synthèse (0 : « développeuse », 1 : « fabieng »)


class Series(models.Model):
    class Status(models.TextChoices):
        WRITING = "redaction", "En rédaction"
        PENDING = "attente", "En file d'attente"
        GENERATING = "generation", "Écriture en cours"
        REVIEW = "a_relire", "À relire"
        TO_RECORD = "a_enregistrer", "Validé, à enregistrer"
        RECORDING = "enregistrement", "Enregistrement en cours"
        PUBLISHED = "publie", "Publié"
        DISCARDED = "abandonne", "Abandonné"
        FAILED = "echec", "Échec"

    ACTIVE = (Status.PENDING, Status.GENERATING, Status.REVIEW, Status.TO_RECORD, Status.RECORDING)

    # File : ordre manuel d'abord, puis les enregistrements validés (« a_enregistrer »), puis ordre
    # d'arrivée.
    QUEUE_ORDER = (F("queue_rank").asc(nulls_last=True), "status", "requested_at")

    category = models.ForeignKey(GuideCategory, verbose_name="thème", on_delete=models.CASCADE,
                                 related_name="podcast_series")
    title = models.CharField("titre", max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    summary = models.TextField("présentation", blank=True)
    level = models.CharField("niveau", max_length=20, choices=Level.choices, default=Level.BEGINNER)
    plan = models.JSONField("plan des épisodes (génération)", default=list, blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="rédacteur",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Écrit dans l'atelier de création (vide : généré par l'IA pour un thème).",
    )
    brief = models.TextField(
        "consignes de rédaction",
        blank=True,
        help_text="Ce dont le rédacteur veut parler : guide l'IA quand elle écrit ou aide.",
    )
    status = models.CharField("état", max_length=16, choices=Status.choices, default=Status.PENDING)
    llm_model = models.CharField("modèle d'IA", max_length=80, blank=True)
    voice = models.CharField("voix", max_length=120, blank=True)
    progress = models.CharField("avancement", max_length=120, blank=True)
    error = models.TextField("erreur", blank=True)
    duration_seconds = models.FloatField("durée totale (s)", default=0)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="demandé par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    requested_at = models.DateTimeField("demandé le", default=timezone.now)
    queue_rank = models.PositiveIntegerField(
        "rang dans la file",
        null=True,
        blank=True,
        help_text="Ordre choisi à la main (vide : ordre automatique).",
    )
    started_at = models.DateTimeField("étape commencée le", null=True, blank=True)
    generated_at = models.DateTimeField("script écrit le", null=True, blank=True)
    # « started_at » repart à zéro à chaque étape : la durée de chacune est gardée ici (tableau de
    # bord).
    script_seconds = models.FloatField("durée d'écriture (s)", null=True, blank=True)
    recording_seconds = models.FloatField("durée d'enregistrement (s)", null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="validé par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    approved_at = models.DateTimeField("validé le", null=True, blank=True)
    published_at = models.DateTimeField("publié le", null=True, blank=True)

    class Meta:
        verbose_name = "série de podcasts"
        verbose_name_plural = "séries de podcasts"
        ordering = ["category__position", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("podcasts:series_detail", args=[self.slug])

    @property
    def duration_display(self):
        minutes = round(self.duration_seconds / 60)
        return (
            f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{max(minutes, 1)} min"
        )


class Episode(models.Model):
    series = models.ForeignKey(
        Series, verbose_name="série", on_delete=models.CASCADE, related_name="episodes"
    )
    position = models.PositiveSmallIntegerField("ordre")
    title = models.CharField("titre", max_length=200)
    summary = models.CharField("résumé", max_length=400, blank=True)
    script = models.JSONField(
        "script",
        default=list,
        help_text="Répliques : [{« speaker » : 0 (Clara) ou 1 (Hugo), « text » : …}, …]",
    )
    sources = models.CharField("guides utilisés", max_length=500, blank=True)
    flags = models.JSONField(
        "répliques à vérifier",
        default=list,
        blank=True,
        help_text="Vérification par l'IA : [{« index » : n° de réplique (0…), « reason » : …}]",
    )
    audio = models.FileField("fichier audio", upload_to="podcasts/", blank=True)
    duration_seconds = models.FloatField("durée (s)", default=0)
    needs_recording = models.BooleanField(
        "à réenregistrer",
        default=False,
        help_text=(
            "Script modifié après l'enregistrement : l'ancien audio reste écouté "
            "jusqu'au nouvel enregistrement."
        ),
    )

    class Meta:
        verbose_name = "épisode"
        verbose_name_plural = "épisodes"
        ordering = ["series", "position"]
        constraints = [
            models.UniqueConstraint(fields=["series", "position"], name="episode_unique")
        ]

    def __str__(self):
        return f"{self.series} — {self.position}. {self.title}"

    @property
    def clock(self):
        minutes, seconds = divmod(int(round(self.duration_seconds)), 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def lines(self):
        """Répliques avec le nom de qui parle (et la remarque de la vérification, pour la
        relecture)."""
        reasons = {flag["index"]: flag["reason"] for flag in self.flags}
        return [{"host": HOSTS[turn["speaker"]], "speaker": turn["speaker"], "text": turn["text"],
                 "flag": reasons.get(index, "")} for index, turn in enumerate(self.script)]

    @property
    def words(self):
        return sum(len(turn["text"].split()) for turn in self.script)


class PodcastProgress(models.Model):
    """Où en est l'écoute d'une série (reprise) et quels épisodes ont été écoutés jusqu'au bout."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.CASCADE,
        related_name="podcast_progress",
    )
    series = models.ForeignKey(
        Series, verbose_name="série", on_delete=models.CASCADE, related_name="listeners"
    )
    episode = models.PositiveSmallIntegerField("épisode en cours", default=1)
    seconds = models.FloatField("position (s)", default=0)
    episodes_done = models.JSONField("épisodes écoutés", default=list)
    completed_at = models.DateTimeField("série terminée le", null=True, blank=True)
    updated_at = models.DateTimeField("mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "écoute d'une série"
        verbose_name_plural = "écoutes des séries"
        constraints = [
            models.UniqueConstraint(fields=["user", "series"], name="ecoute_podcast_unique")
        ]
