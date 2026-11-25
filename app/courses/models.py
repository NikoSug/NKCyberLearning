"""
Cours générés par l'IA locale à partir des guides de l'ANSSI, relus avant publication.

  - Cours thématique : un domaine (catégorie de guides), expliqué pas à pas en leçons courtes.
  - Cours d'un guide : la version simplifiée d'un guide, découpée en leçons.

Chaque leçon se termine par un mini-quiz (correction immédiate) qu'il faut réussir pour
ouvrir la suivante ; un examen final valide le cours.
"""
from django.conf import settings
from django.db import models
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Guide, GuideCategory, Level

LESSON_PASS = 0.75   # part de bonnes réponses pour réussir le quiz d'une leçon (3 sur 4)
EXAM_PASS = 0.7      # pour réussir l'examen final


class Course(models.Model):
    class Kind(models.TextChoices):
        THEME = "theme", "Cours thématique"
        GUIDE = "guide", "Cours d'un guide"

    class Status(models.TextChoices):
        WRITING = "redaction", "En rédaction"
        PENDING = "attente", "En file d'attente"
        GENERATING = "generation", "Génération en cours"
        REVIEW = "a_relire", "À relire"
        PUBLISHED = "publie", "Publié"
        DISCARDED = "abandonne", "Abandonné"
        FAILED = "echec", "Échec"

    ACTIVE = (Status.PENDING, Status.GENERATING, Status.REVIEW)

    # File : ordre manuel d'abord, puis les cours thématiques, puis ordre d'arrivée.
    QUEUE_ORDER = (F("queue_rank").asc(nulls_last=True), "-kind", "requested_at")

    kind = models.CharField("type", max_length=10, choices=Kind.choices)
    category = models.ForeignKey(GuideCategory, verbose_name="thème", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="courses")
    guide = models.ForeignKey(Guide, verbose_name="guide", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="courses")
    title = models.CharField("titre", max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    summary = models.TextField("présentation", blank=True)
    level = models.CharField("niveau", max_length=20, choices=Level.choices, default=Level.BEGINNER)
    plan = models.JSONField("plan des leçons (génération)", default=list, blank=True)
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
    status = models.CharField("état", max_length=12, choices=Status.choices, default=Status.PENDING)
    llm_model = models.CharField("modèle d'IA", max_length=80, blank=True)
    progress = models.CharField("avancement", max_length=120, blank=True)
    error = models.TextField("erreur", blank=True)
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
    started_at = models.DateTimeField("génération commencée le", null=True, blank=True)
    generated_at = models.DateTimeField("généré le", null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="publié par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    published_at = models.DateTimeField("publié le", null=True, blank=True)
    prereviewed_at = models.DateTimeField("pré-relu par l'IA le", null=True, blank=True)
    prereview = models.JSONField(
        "pré-relecture par l'IA",
        default=dict,
        blank=True,
        help_text="Étapes relues et notes du pré-relecteur IA (voir catalogue/prereview.py).",
    )

    class Meta:
        verbose_name = "cours"
        verbose_name_plural = "cours"
        ordering = ["kind", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("courses:course_detail", args=[self.slug])

    @property
    def source_title(self):
        return self.category.name if self.category_id else self.guide.title if self.guide_id else ""


class Lesson(models.Model):
    course = models.ForeignKey(
        Course, verbose_name="cours", on_delete=models.CASCADE, related_name="lessons"
    )
    position = models.PositiveSmallIntegerField("ordre")
    title = models.CharField("titre", max_length=200)
    objective = models.CharField("objectif", max_length=300, blank=True)
    body = models.TextField(
        "contenu", help_text="Texte simple : « ## Titre », listes « - … », **gras**."
    )
    key_points = models.JSONField("à retenir", default=list, blank=True)
    sources = models.CharField("sources", max_length=500, blank=True)
    minutes = models.PositiveSmallIntegerField("durée de lecture (min)", default=5)

    class Meta:
        verbose_name = "leçon"
        verbose_name_plural = "leçons"
        ordering = ["course", "position"]
        constraints = [models.UniqueConstraint(fields=["course", "position"], name="lecon_unique")]

    def __str__(self):
        return f"{self.course} — {self.position}. {self.title}"

    def get_absolute_url(self):
        return reverse("courses:lesson", args=[self.course.slug, self.position])


class CourseQuestion(models.Model):
    """Question du quiz d'une leçon, ou de l'examen final (`lesson` vide)."""

    course = models.ForeignKey(
        Course, verbose_name="cours", on_delete=models.CASCADE, related_name="questions"
    )
    lesson = models.ForeignKey(
        Lesson,
        verbose_name="leçon (vide : examen final)",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    position = models.PositiveSmallIntegerField("ordre", default=0)
    text = models.TextField("question")
    choices = models.JSONField("propositions")
    answer = models.PositiveSmallIntegerField(
        "indice de la bonne réponse",
        default=0,
        help_text=(
            "0 dans un brouillon (première proposition) ; "
            "les propositions sont mélangées à la publication."
        ),
    )
    explanation = models.TextField("explication", blank=True)

    class Meta:
        verbose_name = "question de cours"
        verbose_name_plural = "questions de cours"
        ordering = ["course", "lesson__position", "position"]

    def __str__(self):
        return self.text[:80]


class LessonProgress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.CASCADE,
        related_name="lesson_progress",
    )
    lesson = models.ForeignKey(
        Lesson, verbose_name="leçon", on_delete=models.CASCADE, related_name="progress"
    )
    attempts = models.PositiveSmallIntegerField("essais", default=0)
    best_score = models.PositiveSmallIntegerField("meilleur score (%)", default=0)
    passed_at = models.DateTimeField("réussie le", null=True, blank=True)
    updated_at = models.DateTimeField("mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "progression dans une leçon"
        verbose_name_plural = "progressions dans les leçons"
        constraints = [
            models.UniqueConstraint(fields=["user", "lesson"], name="progression_lecon_unique")
        ]


class ExamAttempt(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.CASCADE,
        related_name="exam_attempts",
    )
    course = models.ForeignKey(
        Course, verbose_name="cours", on_delete=models.CASCADE, related_name="exam_attempts"
    )
    score = models.PositiveSmallIntegerField("bonnes réponses")
    total = models.PositiveSmallIntegerField("questions")
    answers = models.JSONField("réponses", default=dict)
    created_at = models.DateTimeField("passé le", default=timezone.now)

    class Meta:
        verbose_name = "examen de cours"
        verbose_name_plural = "examens de cours"
        ordering = ["-created_at"]

    @property
    def percent(self):
        return round(100 * self.score / self.total) if self.total else 0

    @property
    def passed(self):
        return self.total and self.score / self.total >= EXAM_PASS
