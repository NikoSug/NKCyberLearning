from datetime import time

from django import forms

from .models import StudyPlan

AVAILABILITY_CHOICES = [
    (15, "15 minutes par jour"),
    (30, "30 minutes par jour"),
    (45, "45 minutes par jour"),
    (60, "1 heure par jour"),
    (90, "1 h 30 par jour"),
    (120, "2 heures par jour"),
    (180, "3 heures par jour"),
    (240, "4 heures par jour"),
]


WEEKDAY_CHOICES = [
    (0, "Lun"),
    (1, "Mar"),
    (2, "Mer"),
    (3, "Jeu"),
    (4, "Ven"),
    (5, "Sam"),
    (6, "Dim"),
]


class StudyPlanForm(forms.ModelForm):
    minutes_per_day = forms.TypedChoiceField(
        label="Temps que je peux consacrer à la lecture", choices=AVAILABILITY_CHOICES, coerce=int)
    weekdays = forms.TypedMultipleChoiceField(
        label="Les jours où je lis",
        choices=WEEKDAY_CHOICES,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Choisissez au moins un jour."},
    )

    reading_time = forms.TimeField(
        label="À quelle heure", required=False,
        widget=forms.TimeInput(attrs={"type": "time", "step": 900}, format="%H:%M"),
        help_text="Heure des rendez-vous de lecture ajoutés à votre agenda.")

    class Meta:
        model = StudyPlan
        fields = ["minutes_per_day", "weekdays", "reading_time"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial.setdefault(
                "weekdays", [0, 1, 2, 3, 4]
            )  # nouveau planning : jours ouvrés proposés

    def clean_weekdays(self):
        return sorted(set(self.cleaned_data["weekdays"]))

    def clean_reading_time(self):
        """Heure facultative : à défaut, celle déjà choisie ou 12 h 30."""
        return self.cleaned_data["reading_time"] or self.instance.reading_time or time(12, 30)
