from datetime import time

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from .validators import validate_pdf, validate_xlsx


def _duration(total_minutes):
    hours, minutes = divmod(total_minutes, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} h {minutes:02d}" if minutes else f"{hours} h"


class Level(models.TextChoices):
    BEGINNER = "1-debutant", "Débutant"
    INTERMEDIATE = "2-intermediaire", "Intermédiaire"
    ADVANCED = "3-avance", "Avancé"


class Language(models.TextChoices):
    FR = "fr", "Français"
    EN = "en", "Anglais"


class Editorial(models.Model):
    """
    Contribution écrite dans l'atelier de création : auteur et état de rédaction.

    Un contenu ajouté par un rédacteur reste invisible (`is_published` faux) tant qu'un gestionnaire
    de contenu ne l'a pas publié ; l'état vide désigne un contenu publié ou importé (historique).
    """

    class EditorialStatus(models.TextChoices):
        PUBLISHED = "", "Publié"
        WRITING = "redaction", "En rédaction"
        REVIEW = "a_relire", "À relire"

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="rédacteur",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    editorial_status = models.CharField(
        "état de rédaction", max_length=12, choices=EditorialStatus.choices, blank=True, default=""
    )
    submitted_at = models.DateTimeField("soumis le", null=True, blank=True)

    class Meta:
        abstract = True


class GuideCategory(models.Model):
    """Domaine de la cybersécurité regroupant des guides (sert aussi de liste de lecture)."""

    name = models.CharField("nom", max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField("description", blank=True)
    position = models.PositiveSmallIntegerField("ordre d'affichage", default=100)

    class Meta:
        verbose_name = "catégorie de guide"
        verbose_name_plural = "catégories de guides"
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


ANSSI_DOMAINS = ("cyber.gouv.fr", "ssi.gouv.fr")


def is_anssi_url(url):
    host = (url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    return any(host == d or host.endswith("." + d) for d in ANSSI_DOMAINS)


class Guide(Editorial):
    """Un guide publié par l'ANSSI, stocké et consultable dans l'application."""

    anssi_id = models.CharField(
        "identifiant ANSSI", max_length=200, unique=True, null=True, blank=True,
        help_text="Renseigné par l'import automatique ; vide pour un guide ajouté à la main.")
    title = models.CharField("titre", max_length=255)
    slug = models.SlugField(max_length=280, unique=True)
    category = models.ForeignKey(
        GuideCategory, verbose_name="catégorie", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guides")
    collection = models.CharField("collection ANSSI", max_length=120, blank=True)
    theme = models.CharField("thématique ANSSI", max_length=120, blank=True)
    needs = models.JSONField("besoins couverts", default=list, blank=True)
    language = models.CharField(
        "langue", max_length=2, choices=Language.choices, default=Language.FR
    )
    level = models.CharField(
        "niveau", max_length=20, choices=Level.choices, default=Level.INTERMEDIATE
    )
    page_count = models.PositiveIntegerField("nombre de pages", null=True, blank=True)
    estimated_minutes = models.PositiveIntegerField(
        "temps de lecture estimé (min)", default=30,
        validators=[MinValueValidator(1), MaxValueValidator(6000)])
    summary = models.TextField("résumé", blank=True)
    version = models.CharField("version", max_length=50, blank=True)
    published_on = models.DateField("date de publication ANSSI", null=True, blank=True)
    source_url = models.URLField("page officielle ANSSI", max_length=500, blank=True)
    cover = models.FileField(
        "illustration",
        upload_to="guides/couvertures/",
        blank=True,
        validators=[FileExtensionValidator(["avif", "webp", "png", "jpg", "jpeg"])],
    )
    is_published = models.BooleanField("visible sur la plateforme", default=True)
    created_at = models.DateTimeField("ajouté le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "guide ANSSI"
        verbose_name_plural = "guides ANSSI"
        ordering = ["title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("catalogue:guide_detail", args=[self.slug])

    @property
    def is_anssi(self):
        """Publication de l'ANSSI (Licence Ouverte v2.0 : la source et la date doivent être
        citées)."""
        return bool(self.anssi_id) or is_anssi_url(self.source_url)

    @property
    def duration_display(self):
        return _duration(self.estimated_minutes)

    @property
    def needs_display(self):
        return [NEEDS_LABELS.get(n, n) for n in self.needs]


NEEDS_LABELS = {
    "SECURISER": "Sécuriser",
    "REAGIR": "Réagir",
    "ETRE_SENSIBILISE": "Se sensibiliser",
    "SE_FORMER": "Se former",
}


class GuideDocument(models.Model):
    """Un fichier rattaché à un guide (un guide ANSSI peut en compter plusieurs)."""

    guide = models.ForeignKey(Guide, verbose_name="guide", on_delete=models.CASCADE,
                              related_name="documents")
    label = models.CharField("libellé", max_length=255)
    file = models.FileField("fichier PDF", upload_to="guides/%Y/", blank=True,
                            validators=[validate_pdf])
    source_url = models.URLField("adresse d'origine", max_length=500, blank=True)
    page_count = models.PositiveIntegerField("nombre de pages", null=True, blank=True)
    position = models.PositiveSmallIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "document"
        verbose_name_plural = "documents"
        ordering = ["position", "pk"]

    def __str__(self):
        return self.label


def all_weekdays():
    return [0, 1, 2, 3, 4, 5, 6]


class StudyPlan(models.Model):
    """Disponibilité déclarée par l'utilisateur : son planning répartit « Ma lecture » jour par
    jour."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="study_plan")
    minutes_per_day = models.PositiveIntegerField(
        "temps disponible par jour (min)", default=30,
        validators=[MinValueValidator(5), MaxValueValidator(10 * 60)])
    weekdays = models.JSONField("jours de lecture (0 = lundi … 6 = dimanche)", default=all_weekdays)
    reading_time = models.TimeField(
        "heure de lecture (rendez-vous de l'agenda)", default=time(12, 30)
    )
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "planning de lecture"
        verbose_name_plural = "plannings de lecture"

    def __str__(self):
        return f"Planning de {self.user}"

    WEEKDAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

    @property
    def weekdays_display(self):
        days = sorted(self.weekdays or all_weekdays())
        if len(days) == 7:
            return "tous les jours"
        if days == [0, 1, 2, 3, 4]:
            return "du lundi au vendredi"
        return ", ".join(self.WEEKDAY_NAMES[d] for d in days)


class Referential(Editorial):
    """Référentiel d'exigences (ANSSI) ou programme d'examen d'une certification professionnelle."""

    class Kind(models.TextChoices):
        ANSSI = "anssi", "Référentiel ANSSI"
        EXAM = "exam", "Programme d'examen"
        STANDARD = "standard", "Norme"

    name = models.CharField("nom", max_length=255)
    short_name = models.CharField("nom court", max_length=60, blank=True)
    slug = models.SlugField(max_length=200, unique=True)
    kind = models.CharField("type", max_length=10, choices=Kind.choices, default=Kind.ANSSI)
    version = models.CharField("version", max_length=60, blank=True)
    issuer = models.CharField("éditeur", max_length=120, blank=True)
    description = models.TextField("description", blank=True)
    source_url = models.URLField("source officielle", max_length=500, blank=True)
    grid_source = models.CharField(
        "origine de la grille",
        max_length=255,
        blank=True,
        help_text=(
            "D'où proviennent les exigences (trame d'évaluation, sommaire du PDF, programme"
            " officiel)."
        ),
    )
    document = models.FileField("document PDF", upload_to="referentiels/", blank=True,
                                validators=[validate_pdf])
    page_count = models.PositiveIntegerField("nombre de pages", null=True, blank=True)
    estimated_minutes = models.PositiveIntegerField("temps de lecture estimé (min)", default=60)
    trame = models.FileField(
        "trame d'évaluation (Excel)",
        upload_to="referentiels/trames/",
        blank=True,
        validators=[validate_xlsx],
    )
    trame_label = models.CharField("libellé de la trame", max_length=255, blank=True)
    is_published = models.BooleanField("visible sur la plateforme", default=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "référentiel"
        verbose_name_plural = "référentiels"
        ordering = ["kind", "name"]

    def __str__(self):
        return f"{self.name} {self.version}".strip()

    def get_absolute_url(self):
        return reverse("catalogue:referential_detail", args=[self.slug])

    @property
    def is_anssi(self):
        return self.issuer.upper().startswith("ANSSI") or is_anssi_url(self.source_url)

    @property
    def is_third_party(self):
        """Programme d'examen publié par un autre détenteur (marque, page officielle qui fait
        foi)."""
        return self.kind == self.Kind.EXAM and not self.is_anssi

    @property
    def title(self):
        if (
            self.short_name and self.kind != self.Kind.EXAM
        ):  # « Programme d'examen CISSP » se suffit
            return f"{self.short_name} — {self.name}"
        return self.name

    @property
    def version_display(self):
        return f"v{self.version}" if self.version[:1].isdigit() else self.version

    @property
    def duration_display(self):
        return _duration(self.estimated_minutes)


class GuideCitation(models.Model):
    """Document cité par un référentiel (annexe « Références documentaires »), relié au guide
    importé."""

    referential = models.ForeignKey(
        Referential, verbose_name="référentiel", on_delete=models.CASCADE, related_name="citations"
    )
    code = models.CharField("renvoi", max_length=60, blank=True)
    title = models.CharField("titre cité", max_length=500)
    guide = models.ForeignKey(Guide, verbose_name="guide correspondant", on_delete=models.SET_NULL,
                              null=True, blank=True, related_name="citations")
    position = models.PositiveSmallIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "document cité"
        verbose_name_plural = "documents cités"
        ordering = ["referential", "position"]

    def __str__(self):
        return f"[{self.code}] {self.title}" if self.code else self.title


class Requirement(models.Model):
    """Ligne d'une grille : exigence, point de vérification ou domaine d'examen."""

    class Kind(models.TextChoices):
        REQUIREMENT = "requirement", "Exigence"
        RECOMMENDATION = "recommendation", "Recommandation"
        VERIFICATION = "verification", "Vérification"
        DOMAIN = "domain", "Domaine"
        SECTION = "section", "Section"

    TRACKED_KINDS = (Kind.REQUIREMENT, Kind.RECOMMENDATION, Kind.DOMAIN, Kind.SECTION)

    referential = models.ForeignKey(
        Referential, on_delete=models.CASCADE, related_name="requirements"
    )
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="checks")
    kind = models.CharField("type", max_length=20, choices=Kind.choices, default=Kind.REQUIREMENT)
    ref = models.CharField("référence", max_length=60, blank=True)
    theme = models.CharField("thème", max_length=255, blank=True)
    text = models.TextField("libellé")
    guidance = models.TextField("précisions", blank=True)
    evidence = models.TextField("preuves attendues", blank=True)
    weight = models.CharField("pondération", max_length=30, blank=True)
    position = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "exigence"
        verbose_name_plural = "exigences"
        ordering = ["referential", "position"]

    def __str__(self):
        return f"{self.ref} {self.text[:80]}".strip()


class RequirementProgress(models.Model):
    """Où en est un utilisateur sur une ligne de grille."""

    class Status(models.TextChoices):
        TODO = "todo", "À faire"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Validé"
        NOT_APPLICABLE = "na", "Non applicable"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="requirement_progress")
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="progress")
    status = models.CharField("statut", max_length=12, choices=Status.choices, default=Status.TODO)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "suivi d'exigence"
        verbose_name_plural = "suivis d'exigences"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "requirement"], name="unique_progress_per_requirement"
            )
        ]


class Certification(Editorial):
    """Une certification ou qualification : dispositif ANSSI ou certification professionnelle."""

    class Kind(models.TextChoices):
        PRODUCT_CERTIFICATION = "product_cert", "Certification de produit"
        PRODUCT_QUALIFICATION = "product_qual", "Qualification de produit"
        PROVIDER_QUALIFICATION = "provider_qual", "Qualification de prestataire"
        INDIVIDUAL = "individual", "Certification professionnelle"
        OTHER = "other", "Autre"

    name = models.CharField("nom", max_length=255)
    acronym = models.CharField("sigle", max_length=40, blank=True)
    slug = models.SlugField(max_length=280, unique=True)
    kind = models.CharField("type", max_length=20, choices=Kind.choices, default=Kind.OTHER)
    issuer = models.CharField("organisme", max_length=120, blank=True)
    domain = models.CharField("domaine", max_length=120, blank=True)
    level = models.CharField("niveau", max_length=20, choices=Level.choices, blank=True)
    audience = models.TextField("public visé", blank=True)
    prerequisites = models.TextField("prérequis", blank=True)
    exam_format = models.TextField("format de l'évaluation", blank=True)
    validity = models.CharField("validité / maintien", max_length=255, blank=True)
    description = models.TextField("description", blank=True)
    source_url = models.URLField("page officielle", max_length=500, blank=True)
    referentials = models.ManyToManyField(Referential, verbose_name="référentiels", blank=True,
                                          related_name="certifications")
    guide_categories = models.ManyToManyField(
        GuideCategory,
        verbose_name="domaines de guides conseillés",
        blank=True,
        related_name="certifications",
        help_text=(
            "Pour une certification sans référentiel ANSSI : domaines utiles à sa préparation."
        ),
    )
    is_published = models.BooleanField("visible sur la plateforme", default=True)
    created_at = models.DateTimeField("ajoutée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        verbose_name = "certification"
        verbose_name_plural = "certifications"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.acronym})" if self.acronym else self.name

    def get_absolute_url(self):
        return reverse("catalogue:certification_detail", args=[self.slug])


class ReadingListItem(models.Model):
    """
    Élément de « Ma lecture » : un guide ou un référentiel choisi par l'utilisateur.

    Cycle : ajouté -> commencé (première ouverture du document) -> lu (déclaré
    par l'utilisateur). Un futur module de test viendra valider la lecture :
    il s'ajoutera après « lu » (voir catalogue/reading.py) sans changer ce cycle.
    """

    class Status(models.TextChoices):
        TO_READ = "to_read", "À lire"
        READING = "reading", "En cours"
        FINISHED = "finished", "Lu"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="reading_items")
    guide = models.ForeignKey(Guide, verbose_name="guide", on_delete=models.CASCADE,
                              null=True, blank=True, related_name="reading_items")
    referential = models.ForeignKey(
        Referential,
        verbose_name="référentiel",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reading_items",
    )
    position = models.PositiveIntegerField("ordre", default=0)
    added_at = models.DateTimeField("ajouté le", default=timezone.now)
    started_at = models.DateTimeField("commencé le", null=True, blank=True)
    finished_at = models.DateTimeField("lu le", null=True, blank=True)

    class Meta:
        verbose_name = "élément de lecture"
        verbose_name_plural = "lectures des utilisateurs"
        ordering = ["position", "added_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(guide__isnull=False, referential__isnull=True)
                    | models.Q(guide__isnull=True, referential__isnull=False)
                ),
                name="reading_item_guide_xor_referential",
            ),
            models.UniqueConstraint(
                fields=["user", "guide"],
                condition=models.Q(guide__isnull=False),
                name="unique_reading_guide_per_user",
            ),
            models.UniqueConstraint(
                fields=["user", "referential"],
                condition=models.Q(referential__isnull=False),
                name="unique_reading_referential_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.document}"

    @property
    def document(self):
        return self.guide or self.referential

    @property
    def is_guide(self):
        return self.guide_id is not None

    @property
    def estimated_minutes(self):
        return self.document.estimated_minutes

    @property
    def status(self):
        if self.finished_at:
            return self.Status.FINISHED
        return self.Status.READING if self.started_at else self.Status.TO_READ

    def get_status_display(self):
        return self.Status(self.status).label


DIAGRAM_HELP = (
    "Facultatif : schéma d'exemple en syntaxe Mermaid simplifiée (« A[Poste] -->|HTTPS| B[Serveur]"
    " »), dessiné sous la question. Voir catalogue/diagrams.py."
)


def guide_xor_referential(name):
    """Contrainte des objets de QCM : rattachés à un guide ou à un référentiel ANSSI, jamais aux
    deux."""
    return models.CheckConstraint(
        condition=(
            models.Q(guide__isnull=False, referential__isnull=True)
            | models.Q(guide__isnull=True, referential__isnull=False)
        ),
        name=name,
    )


class QuizSubject:
    """Le guide ou le référentiel interrogé."""

    @property
    def subject(self):
        return self.guide or self.referential


def subject_filter(subject):
    """Filtre des objets de QCM d'un guide ou d'un référentiel :
    QuizQuestion.objects.filter(**subject_filter(s))."""
    return {"referential": subject} if isinstance(subject, Referential) else {"guide": subject}


class QuizQuestion(QuizSubject, models.Model):
    """
    Question à choix multiples sur un guide ou un référentiel ANSSI. Les questions sont
    réparties en séries numérotées de 1 (les plus simples) à 6 (les plus difficiles).
    """

    guide = models.ForeignKey(
        Guide,
        verbose_name="guide",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quiz_questions",
    )
    referential = models.ForeignKey(
        Referential,
        verbose_name="référentiel",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quiz_questions",
    )
    series = models.PositiveSmallIntegerField("série", validators=[MinValueValidator(1)])
    position = models.PositiveSmallIntegerField("ordre dans la série", default=0)
    text = models.TextField("question")
    choices = models.JSONField("propositions")
    answer = models.PositiveSmallIntegerField("indice de la bonne réponse")
    explanation = models.TextField("explication", blank=True)
    source = models.CharField("source dans le guide", max_length=120, blank=True)
    diagram = models.TextField("schéma d'exemple", blank=True, help_text=DIAGRAM_HELP)

    class Meta:
        verbose_name = "question de test"
        verbose_name_plural = "questions de test"
        ordering = ["guide", "series", "position"]
        constraints = [guide_xor_referential("quiz_question_guide_xor_referential")]

    def __str__(self):
        return f"{self.subject} — série {self.series} — {self.text[:60]}"


class QuizAttempt(QuizSubject, models.Model):
    """Une série de questions passée par un utilisateur (on garde toutes les tentatives)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="quiz_attempts")
    guide = models.ForeignKey(
        Guide,
        verbose_name="guide",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quiz_attempts",
    )
    referential = models.ForeignKey(
        Referential,
        verbose_name="référentiel",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quiz_attempts",
    )
    series = models.PositiveSmallIntegerField("série")
    score = models.PositiveSmallIntegerField("bonnes réponses")
    total = models.PositiveSmallIntegerField("questions")
    answers = models.JSONField("réponses données", default=dict)
    created_at = models.DateTimeField("passée le", default=timezone.now)

    class Meta:
        verbose_name = "tentative de test"
        verbose_name_plural = "tentatives de test"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "guide", "series"])]
        constraints = [guide_xor_referential("quiz_attempt_guide_xor_referential")]

    def __str__(self):
        return f"{self.user} — {self.subject} — série {self.series} : {self.score}/{self.total}"

    @property
    def percent(self):
        return round(100 * self.score / self.total) if self.total else 0


class DailyReview(models.Model):
    """
    Révision du jour : questions tirées de tous les guides lus par l'utilisateur.
    La sélection est figée pour la journée (voir catalogue/revision.py).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="utilisateur",
                             on_delete=models.CASCADE, related_name="daily_reviews")
    date = models.DateField("jour")
    # 1 : révision du jour ; 2 : seconde révision achetée en boutique (nouvelles questions).
    number = models.PositiveSmallIntegerField("numéro dans la journée", default=1)
    question_ids = models.JSONField("questions (dans l'ordre)", default=list)
    created_at = models.DateTimeField("créée le", default=timezone.now)

    class Meta:
        verbose_name = "révision du jour"
        verbose_name_plural = "révisions du jour"
        ordering = ["-date", "number"]
        constraints = [
            models.UniqueConstraint(fields=["user", "date", "number"], name="une_revision_par_jour")
        ]

    def __str__(self):
        suffix = f" (n° {self.number})" if self.number > 1 else ""
        return f"{self.user} — révision du {self.date:%d/%m/%Y}{suffix}"


class DailyReviewAttempt(models.Model):
    """Une réponse à la révision du jour (on peut la refaire ; toutes les tentatives sont
    gardées)."""

    review = models.ForeignKey(DailyReview, verbose_name="révision", on_delete=models.CASCADE,
                               related_name="attempts")
    score = models.PositiveSmallIntegerField("bonnes réponses")
    total = models.PositiveSmallIntegerField("questions")
    answers = models.JSONField("réponses données", default=dict)
    created_at = models.DateTimeField("passée le", default=timezone.now)

    class Meta:
        verbose_name = "tentative de révision"
        verbose_name_plural = "tentatives de révision"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.review} : {self.score}/{self.total}"

    @property
    def percent(self):
        return round(100 * self.score / self.total) if self.total else 0


class NewsItem(models.Model):
    """
    Actualité cyber récupérée depuis un flux RSS public (CERT-FR, Cybermalveillance,
    presse spécialisée) ou fuite de données recensée par « C'est qui qui a fuité
    aujourd'hui ? ». Seul du texte brut est conservé : rien n'est rendu en HTML.
    """

    class Kind(models.TextChoices):
        NEWS = "actu", "Actualité"
        ALERT = "alerte", "Alerte de sécurité"
        LEAK = "fuite", "Fuite de données"

    source = models.CharField("source", max_length=40)
    kind = models.CharField("type", max_length=10, choices=Kind.choices, default=Kind.NEWS)
    guid = models.CharField("identifiant dans le flux", max_length=255)
    title = models.CharField("titre", max_length=300)
    url = models.URLField("lien", max_length=500)
    summary = models.TextField("résumé", blank=True)
    details = models.JSONField("détails (données exposées…)", default=list, blank=True)
    confirmed = models.BooleanField("fuite confirmée", null=True, blank=True)
    sensitive = models.BooleanField("données sensibles", default=False)
    published_at = models.DateTimeField("publiée le")
    fetched_at = models.DateTimeField("récupérée le", default=timezone.now)

    class Meta:
        verbose_name = "actualité"
        verbose_name_plural = "actualités"
        ordering = ["-published_at"]
        constraints = [models.UniqueConstraint(fields=["source", "guid"], name="actualite_unique")]
        indexes = [models.Index(fields=["kind", "-published_at"])]

    def __str__(self):
        return f"[{self.source}] {self.title}"


class Narration(models.Model):
    """
    Version audio d'un guide, lue par synthèse vocale (Kyutai TTS) à partir du texte du PDF réécrit
    pour l'oral.
    Générée à la demande par le service « narrator » (commande `narrate`), section par section.
    """

    class Status(models.TextChoices):
        PENDING = "attente", "En file d'attente"
        PROCESSING = "en_cours", "En cours de génération"
        READY = "prete", "Prête"
        FAILED = "echec", "Échec"

    # File : demandes des utilisateurs d'abord (priorité 0), puis ordre manuel, puis ordre
    # d'arrivée.
    QUEUE_ORDER = ("priority", F("queue_rank").asc(nulls_last=True), "requested_at")

    guide = models.OneToOneField(
        Guide, verbose_name="guide", on_delete=models.CASCADE, related_name="narration"
    )
    status = models.CharField("état", max_length=10, choices=Status.choices, default=Status.PENDING)
    priority = models.PositiveSmallIntegerField(
        "priorité (0 = demandée par un utilisateur)", default=0
    )
    voice = models.CharField("voix", max_length=60, blank=True)
    version = models.PositiveSmallIntegerField("version du nettoyage du texte", default=0)
    sections_total = models.PositiveSmallIntegerField("sections prévues", default=0)
    sections_done = models.PositiveSmallIntegerField("sections générées", default=0)
    duration_seconds = models.FloatField("durée totale (s)", default=0)
    error = models.TextField("erreur", blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="demandée par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    requested_at = models.DateTimeField("demandée le", default=timezone.now)
    queue_rank = models.PositiveIntegerField(
        "rang dans la file",
        null=True,
        blank=True,
        help_text="Ordre choisi à la main (vide : ordre automatique).",
    )
    started_at = models.DateTimeField("commencée le", null=True, blank=True)
    finished_at = models.DateTimeField("terminée le", null=True, blank=True)

    class Meta:
        verbose_name = "version audio"
        verbose_name_plural = "versions audio"
        ordering = ["priority", "requested_at"]

    def __str__(self):
        return f"{self.guide} ({self.get_status_display()})"

    @property
    def duration_display(self):
        return _duration(round(self.duration_seconds / 60))


class NarrationSection(models.Model):
    """Une partie (chapitre) de la version audio : texte lu et fichier MP3."""

    narration = models.ForeignKey(Narration, verbose_name="version audio", on_delete=models.CASCADE,
                                  related_name="sections")
    position = models.PositiveSmallIntegerField("ordre")
    title = models.CharField("titre", max_length=255)
    text = models.TextField("texte lu")
    source_hash = models.CharField(
        "empreinte du texte d'origine", max_length=64, blank=True, editable=False
    )
    audio = models.FileField("fichier audio", upload_to="narrations/", blank=True)
    duration_seconds = models.FloatField("durée (s)", default=0)

    class Meta:
        verbose_name = "section audio"
        verbose_name_plural = "sections audio"
        ordering = ["narration", "position"]
        constraints = [
            models.UniqueConstraint(fields=["narration", "position"], name="section_audio_unique")
        ]

    def __str__(self):
        return f"{self.narration.guide} — {self.position}. {self.title}"

    @property
    def clock(self):
        minutes, seconds = divmod(int(round(self.duration_seconds)), 60)
        return f"{minutes}:{seconds:02d}"


class ListeningProgress(models.Model):
    """Où en est l'écoute d'un utilisateur (reprise) et quelles sections il a écoutées jusqu'au
    bout."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.CASCADE,
        related_name="listening",
    )
    narration = models.ForeignKey(Narration, verbose_name="version audio", on_delete=models.CASCADE,
                                  related_name="listeners")
    section = models.PositiveSmallIntegerField("section en cours", default=1)
    seconds = models.FloatField("position dans la section (s)", default=0)
    sections_done = models.JSONField("sections écoutées", default=list)
    completed_at = models.DateTimeField("écoute terminée le", null=True, blank=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)

    class Meta:
        verbose_name = "écoute"
        verbose_name_plural = "écoutes"
        constraints = [
            models.UniqueConstraint(fields=["user", "narration"], name="une_ecoute_par_guide")
        ]

    def __str__(self):
        return f"{self.user} — {self.narration.guide}"


class QuizDraft(QuizSubject, models.Model):
    """
    QCM d'un guide (ou d'un référentiel ANSSI) généré par une IA locale (Ollama) ou écrit par un
    rédacteur,
    en attente de relecture humaine. Rien n'est visible des utilisateurs avant la publication (voir
    quizgen.py).
    """

    class Status(models.TextChoices):
        WRITING = "redaction", "En rédaction"
        PENDING = "attente", "En file d'attente"
        GENERATING = "generation", "Génération en cours"
        REVIEW = "a_relire", "À relire"
        PUBLISHED = "publie", "Publié"
        DISCARDED = "abandonne", "Abandonné"
        FAILED = "echec", "Échec"

    ACTIVE = (Status.PENDING, Status.GENERATING, Status.REVIEW)

    # File : ordre manuel d'abord, puis ordre d'arrivée.
    QUEUE_ORDER = (F("queue_rank").asc(nulls_last=True), "requested_at")

    guide = models.ForeignKey(
        Guide,
        verbose_name="guide",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quiz_drafts",
    )
    referential = models.ForeignKey(
        Referential,
        verbose_name="référentiel",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quiz_drafts",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="rédacteur",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Écrit dans l'atelier de création (vide : généré par l'IA).",
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
    prereview_notes = models.JSONField(
        "notes du pré-relecteur",
        default=list,
        blank=True,
        help_text="Remarques générales du pré-relecteur IA (voir catalogue/prereview.py).",
    )

    class Meta:
        verbose_name = "brouillon de QCM"
        verbose_name_plural = "brouillons de QCM"
        ordering = ["requested_at"]
        constraints = [guide_xor_referential("quiz_draft_guide_xor_referential")]

    def __str__(self):
        return f"QCM de « {self.subject} » ({self.get_status_display()})"


class QuizDraftQuestion(models.Model):
    """Question proposée par l'IA. `choices[0]` est la bonne réponse (mélangées à la
    publication)."""

    class Decision(models.TextChoices):
        PENDING = "a_valider", "À valider"
        ACCEPTED = "acceptee", "Acceptée"
        REJECTED = "rejetee", "Rejetée"

    class Prereview(models.TextChoices):
        NONE = "", "Non pré-relue"
        OK = "ok", "Rien à signaler"
        DOUBT = "douteuse", "Douteuse"
        REGENERATED = "regeneree", "Régénérée"

    draft = models.ForeignKey(
        QuizDraft, verbose_name="brouillon", on_delete=models.CASCADE, related_name="questions"
    )
    series = models.PositiveSmallIntegerField("série", validators=[MinValueValidator(1)])
    position = models.PositiveSmallIntegerField("ordre", default=0)
    text = models.TextField("question")
    choices = models.JSONField("propositions (la première est la bonne)")
    explanation = models.TextField("explication", blank=True)
    source = models.CharField("source dans le guide", max_length=120, blank=True)
    diagram = models.TextField("schéma d'exemple", blank=True, help_text=DIAGRAM_HELP)
    context_title = models.CharField("partie du guide utilisée", max_length=255, blank=True)
    cross_check = models.BooleanField("vérification croisée réussie", null=True, blank=True)
    decision = models.CharField(
        "décision", max_length=10, choices=Decision.choices, default=Decision.PENDING
    )
    edited = models.BooleanField("modifiée par un relecteur", default=False)
    prereview = models.CharField(
        "avis du pré-relecteur IA", max_length=10, choices=Prereview.choices, blank=True
    )
    prereview_note = models.TextField("note du pré-relecteur IA", blank=True)
    prereview_attempts = models.PositiveSmallIntegerField(
        "régénérations par le pré-relecteur", default=0
    )

    class Meta:
        verbose_name = "question proposée"
        verbose_name_plural = "questions proposées"
        ordering = ["draft", "series", "position"]

    def __str__(self):
        return f"{self.draft.subject} — série {self.series} — {self.text[:60]}"


class GuideSection(models.Model):
    """Texte d'un guide par chapitres (extrait du PDF à la première question posée à l'assistant de
    lecture)."""

    guide = models.ForeignKey(
        Guide, verbose_name="guide", on_delete=models.CASCADE, related_name="text_sections"
    )
    position = models.PositiveSmallIntegerField("ordre")
    title = models.CharField("titre", max_length=255)
    text = models.TextField("texte")

    class Meta:
        verbose_name = "chapitre du guide (texte)"
        verbose_name_plural = "chapitres des guides (texte)"
        ordering = ["guide", "position"]
        constraints = [
            models.UniqueConstraint(fields=["guide", "position"], name="section_texte_unique")
        ]

    def __str__(self):
        return f"{self.guide} — {self.position}. {self.title}"


class AssistantQuestion(models.Model):
    """Trace d'une question posée à l'assistant de lecture (sans son texte) : limite par heure et
    statistiques."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.CASCADE,
        related_name="assistant_questions",
    )
    guide = models.ForeignKey(
        Guide,
        verbose_name="guide (vide : question générale)",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    asked_at = models.DateTimeField("posée le", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "question à l'assistant"
        verbose_name_plural = "questions à l'assistant"
        ordering = ["-asked_at"]

    def __str__(self):
        return f"{self.user} — {self.guide or 'question générale'} — {self.asked_at:%d/%m/%Y %H:%M}"


class GenerationControl(models.Model):
    """Pilotage d'un service de génération en arrière-plan : pause et dernière activité (tableau de
    bord)."""

    class Kind(models.TextChoices):
        QUIZ = "qcm", "QCM par IA"
        AUDIO = "audio", "Versions audio"
        COURSE = "cours", "Cours par IA"
        GLOSSARY = "lexique", "Lexiques par IA"
        PODCAST = "podcast", "Podcasts par IA"
        AGENT = "agent", "Agent du poste"  # exécute « Lancer / Arrêter les services »
        GAMING = "jeu", "Mode jeu"  # coupe tout : générations, assistant, modèle déchargé du GPU
        VOICE = (
            "voix",
            "Synthèse vocale en cours",
        )  # met l'assistant en pause (carte graphique occupée)
        PREREVIEW = "prerelu", "Pré-relecture par IA"  # « paused » : pré-relecture désactivée

    kind = models.CharField("génération", max_length=10, choices=Kind.choices, unique=True)
    paused = models.BooleanField("en pause", default=False)
    paused_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="mise en pause par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    paused_at = models.DateTimeField("mise en pause le", null=True, blank=True)
    last_seen = models.DateTimeField("dernière activité du service", null=True, blank=True)
    workers = models.PositiveSmallIntegerField("générations en parallèle", default=1)
    failures = models.PositiveIntegerField("échecs consécutifs", default=0)
    note = models.CharField("motif de la pause", max_length=300, blank=True)
    rank = models.PositiveSmallIntegerField(
        "ordre de passage",
        null=True,
        blank=True,
        help_text="Une génération à la fois : la première de l'ordre qui a du travail.",
    )
    users_first = models.BooleanField(
        "demandes des utilisateurs avant tout",
        default=True,
        help_text=(
            "Versions audio seulement : une demande faite depuis la page d'écoute "
            "passe devant l'ordre de passage."
        ),
    )

    class Meta:
        verbose_name = "pilotage d'une génération"
        verbose_name_plural = "pilotage des générations"

    def __str__(self):
        return f"{self.get_kind_display()}{' (en pause)' if self.paused else ''}"


class Glossary(models.Model):
    """
    Lexique d'un guide : sigles et termes techniques relevés par l'IA locale, avec une définition
    simple ; relu avant d'être affiché pendant la lecture (voir catalogue/glossary.py). Peut aussi
    être
    écrit par un rédacteur dans l'atelier de création.
    """

    class Status(models.TextChoices):
        WRITING = "redaction", "En rédaction"
        PENDING = "attente", "En file d'attente"
        GENERATING = "generation", "Génération en cours"
        REVIEW = "a_relire", "À relire"
        PUBLISHED = "publie", "Publié"
        DISCARDED = "abandonne", "Abandonné"
        FAILED = "echec", "Échec"

    ACTIVE = (Status.PENDING, Status.GENERATING, Status.REVIEW)

    # File : ordre manuel d'abord, puis ordre d'arrivée.
    QUEUE_ORDER = (F("queue_rank").asc(nulls_last=True), "requested_at")

    guide = models.OneToOneField(
        Guide, verbose_name="guide", on_delete=models.CASCADE, related_name="glossary"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="rédacteur",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Écrit dans l'atelier de création (vide : généré par l'IA).",
    )
    status = models.CharField("état", max_length=12, choices=Status.choices, default=Status.PENDING)
    llm_model = models.CharField("modèle d'IA", max_length=80, blank=True)
    progress = models.CharField("avancement", max_length=120, blank=True)
    parts_done = models.PositiveSmallIntegerField("parties analysées", default=0)
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

    class Meta:
        verbose_name = "lexique"
        verbose_name_plural = "lexiques"
        ordering = ["requested_at"]

    def __str__(self):
        return f"Lexique de « {self.guide} » ({self.get_status_display()})"


class GlossaryEntry(models.Model):
    class Kind(models.TextChoices):
        ACRONYM = "sigle", "Sigle"
        TERM = "terme", "Terme"

    glossary = models.ForeignKey(
        Glossary, verbose_name="lexique", on_delete=models.CASCADE, related_name="entries"
    )
    term = models.CharField("terme", max_length=80)
    key = models.CharField("clé de tri et de regroupement", max_length=80, db_index=True)
    kind = models.CharField("type", max_length=6, choices=Kind.choices, default=Kind.TERM)
    expansion = models.CharField("forme développée", max_length=200, blank=True)
    definition = models.TextField("définition")

    class Meta:
        verbose_name = "entrée de lexique"
        verbose_name_plural = "entrées de lexique"
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(fields=["glossary", "key"], name="entree_lexique_unique")
        ]

    def __str__(self):
        return self.term


class ServiceCommand(models.Model):
    """
    Demande de lancement ou d'arrêt des services de génération, faite depuis le tableau de bord.
    Le site n'a aucun accès à Docker : l'agent du poste (scripts/ia-agent.ps1) lit la demande et
    exécute une commande fixée d'avance.
    """

    class Action(models.TextChoices):
        START = "lancer", "Lancer les services"
        STOP = "arreter", "Arrêter les services"

    class Status(models.TextChoices):
        PENDING = "attente", "En attente de l'agent"
        RUNNING = "en_cours", "En cours"
        DONE = "fait", "Faite"
        FAILED = "echec", "Échec"

    action = models.CharField("action", max_length=10, choices=Action.choices)
    status = models.CharField("état", max_length=10, choices=Status.choices, default=Status.PENDING)
    message = models.TextField("compte rendu", blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="demandée par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    requested_at = models.DateTimeField("demandée le", default=timezone.now)
    started_at = models.DateTimeField("prise en charge le", null=True, blank=True)
    finished_at = models.DateTimeField("terminée le", null=True, blank=True)

    class Meta:
        verbose_name = "demande de lancement des services"
        verbose_name_plural = "demandes de lancement des services"
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.get_action_display()} ({self.get_status_display()})"


class ReadingPath(Editorial):
    """Parcours de lecture conçu à la main : des guides choisis, dans un ordre choisi, avec un mot
    par étape."""

    title = models.CharField("titre", max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField("présentation", blank=True)
    level = models.CharField("niveau", max_length=20, choices=Level.choices, default=Level.BEGINNER)
    category = models.ForeignKey(GuideCategory, verbose_name="domaine", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="custom_paths")
    is_published = models.BooleanField("visible sur la plateforme", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "parcours sur mesure"
        verbose_name_plural = "parcours sur mesure"
        ordering = ["level", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("catalogue:custom_path", args=[self.slug])


class ReadingPathStep(models.Model):
    path = models.ForeignKey(
        ReadingPath, verbose_name="parcours", on_delete=models.CASCADE, related_name="steps"
    )
    guide = models.ForeignKey(
        Guide, verbose_name="guide", on_delete=models.CASCADE, related_name="+"
    )
    position = models.PositiveSmallIntegerField("ordre", default=0)
    note = models.CharField("pourquoi ce guide à cette étape", max_length=300, blank=True)

    class Meta:
        verbose_name = "étape de parcours"
        verbose_name_plural = "étapes de parcours"
        ordering = ["path", "position"]
        constraints = [
            models.UniqueConstraint(fields=["path", "guide"], name="etape_parcours_unique")
        ]


class AudioUpload(Editorial):
    """
    Version audio enregistrée par un rédacteur (au lieu de la voix de synthèse) : un fichier par
    chapitre.
    À la publication, elle remplace la version audio du guide (voir studio/audio.py).
    """

    guide = models.ForeignKey(
        Guide, verbose_name="guide", on_delete=models.CASCADE, related_name="audio_uploads"
    )
    note = models.CharField("présentation", max_length=300, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    published_at = models.DateTimeField("publié le", null=True, blank=True)

    class Meta:
        verbose_name = "version audio fournie"
        verbose_name_plural = "versions audio fournies"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.guide} — enregistrement de {self.author or '?'}"


class AudioUploadPart(models.Model):
    upload = models.ForeignKey(
        AudioUpload, verbose_name="version audio", on_delete=models.CASCADE, related_name="parts"
    )
    position = models.PositiveSmallIntegerField("ordre", default=0)
    title = models.CharField("titre du chapitre", max_length=255)
    audio = models.FileField("fichier audio (MP3)", upload_to="narrations/fournies/")
    duration_seconds = models.FloatField("durée (s)", default=0)

    class Meta:
        verbose_name = "chapitre enregistré"
        verbose_name_plural = "chapitres enregistrés"
        ordering = ["upload", "position"]



class ImportedContent(models.Model):
    """
    Contenu reçu par un pack de contenus (voir catalogue/packs.py) : empreinte du contenu tel que
    livré et tel
    qu'enregistré ici. Si l'empreinte locale a changé, le contenu a été modifié sur cette instance :
    une mise à
    jour ne l'écrase pas (sauf si on le force).
    """

    kind = models.CharField("type", max_length=20)
    key = models.CharField("clé", max_length=300)
    pack_digest = models.CharField("empreinte livrée", max_length=64)
    local_digest = models.CharField("empreinte à l'import", max_length=64)
    pack_version = models.CharField("version du pack", max_length=40)
    imported_at = models.DateTimeField("importé le", auto_now=True)

    class Meta:
        verbose_name = "contenu importé"
        verbose_name_plural = "contenus importés"
        constraints = [
            models.UniqueConstraint(fields=["kind", "key"], name="contenu_importe_unique")
        ]

    def __str__(self):
        return f"{self.kind} {self.key}"


class ContentImport(models.Model):
    """Historique des packs de contenus importés (installation et mises à jour)."""

    version = models.CharField("version du pack", max_length=40)
    origin = models.CharField("instance d'origine", max_length=120, blank=True)
    built_at = models.DateTimeField("pack produit le", null=True, blank=True)
    imported_at = models.DateTimeField("importé le", auto_now_add=True)
    forced = models.BooleanField("modifications locales écrasées", default=False)
    report = models.JSONField("bilan", default=dict)

    class Meta:
        verbose_name = "import de contenus"
        verbose_name_plural = "imports de contenus"
        ordering = ["-imported_at"]

    def __str__(self):
        return f"{self.version} ({self.imported_at:%d/%m/%Y})"
