"""Formulaires de l'atelier de création, regroupés en sections (`SECTIONS`) pour un affichage
aéré."""

from django import forms

from catalogue.models import Certification, Guide, GuideCategory, Referential, ReadingPath
from catalogue.validators import validate_pdf


class SectionedForm(forms.ModelForm):
    SECTIONS = []

    def sections(self):
        return [
            (title, [self[name] for name in names if name in self.fields])
            for title, names in self.SECTIONS
        ]


class GuideForm(SectionedForm):
    SECTIONS = [
        ("Le guide", ["title", "summary"]),
        ("Classement", ["category", "level", "language", "estimated_minutes"]),
        ("Origine", ["collection", "theme", "version", "published_on", "source_url"]),
    ]

    class Meta:
        model = Guide
        fields = [
            "title",
            "summary",
            "category",
            "level",
            "language",
            "estimated_minutes",
            "collection",
            "theme",
            "version",
            "published_on",
            "source_url",
        ]
        widgets = {"summary": forms.Textarea(attrs={"rows": 5}),
                   "published_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        help_texts = {
            "estimated_minutes": "Calculé à l'ajout du PDF (2,5 min par page), modifiable.",
            "summary": "Affiché sur la fiche du guide et dans la recherche.",
        }


class CategoryForm(SectionedForm):
    SECTIONS = [("Le domaine", ["name", "description", "position"])]

    class Meta:
        model = GuideCategory
        fields = ["name", "description", "position"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}
        help_texts = {"position": "Ordre d'affichage parmi les domaines (les plus petits d'abord)."}


class ReferentialForm(SectionedForm):
    SECTIONS = [
        ("Le référentiel", ["name", "short_name", "kind", "version", "issuer", "description"]),
        ("Documents", ["document", "trame", "trame_label", "source_url", "estimated_minutes"]),
    ]
    KINDS = [Referential.Kind.ANSSI, Referential.Kind.STANDARD]

    class Meta:
        model = Referential
        fields = [
            "name",
            "short_name",
            "kind",
            "version",
            "issuer",
            "description",
            "document",
            "trame",
            "trame_label",
            "source_url",
            "estimated_minutes",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "document": "PDF officiel ; sa lecture compte comme celle d'un guide.",
            "trame": "Trame d'évaluation Excel (ANSSI) : sert à importer la grille d'exigences.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "kind" in self.fields:
            self.fields["kind"].choices = [(k.value, k.label) for k in self.KINDS]


class ExamProgramForm(ReferentialForm):
    """Programme d'examen d'une certification : un référentiel de type « examen » (domaines
    pondérés)."""
    SECTIONS = [
        ("Le programme", ["name", "short_name", "version", "issuer", "description"]),
        ("Documents", ["document", "source_url", "estimated_minutes"]),
    ]

    class Meta(ReferentialForm.Meta):
        fields = [
            "name",
            "short_name",
            "version",
            "issuer",
            "description",
            "document",
            "source_url",
            "estimated_minutes",
        ]
        labels = {"issuer": "Organisme certificateur"}
        help_texts = {"document": "Programme officiel (PDF), si disponible."}


class CertificationForm(SectionedForm):
    SECTIONS = [
        (
            "La certification",
            ["name", "acronym", "kind", "issuer", "level", "domain", "description"],
        ),
        (
            "Pour qui, comment",
            ["audience", "prerequisites", "exam_format", "validity", "source_url"],
        ),
        ("Liens dans le catalogue", ["referentials", "guide_categories"]),
    ]

    class Meta:
        model = Certification
        fields = [
            "name",
            "acronym",
            "kind",
            "issuer",
            "level",
            "domain",
            "description",
            "audience",
            "prerequisites",
            "exam_format",
            "validity",
            "source_url",
            "referentials",
            "guide_categories",
        ]
        widgets = {name: forms.Textarea(attrs={"rows": 3})
                   for name in ("description", "audience", "prerequisites", "exam_format")}
        help_texts = {
            "referentials": "Référentiels et programmes d'examen sur lesquels elle s'appuie.",
            "guide_categories": "Domaines dont les guides préparent à cette certification.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("referentials", "guide_categories"):
            self.fields[name].widget = forms.CheckboxSelectMultiple(
                choices=self.fields[name].choices
            )
            self.fields[name].widget.attrs["class"] = "check-list"
        self.fields["referentials"].queryset = Referential.objects.filter(
            is_published=True
        ).order_by("kind", "name")


class PathForm(SectionedForm):
    SECTIONS = [("Le parcours", ["title", "description", "level", "category"])]

    class Meta:
        model = ReadingPath
        fields = ["title", "description", "level", "category"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}
        help_texts = {"category": "Facultatif : le domaine sous lequel le parcours apparaît."}


class DocumentForm(forms.Form):
    label = forms.CharField(label="Libellé", max_length=255, initial="Guide (PDF)")
    file = forms.FileField(label="Fichier PDF", validators=[validate_pdf])
