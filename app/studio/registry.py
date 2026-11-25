"""Types de contenu de l'atelier de création : cartes de l'accueil, listes et compteurs."""
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse


@dataclass(frozen=True)
class ContentType:
    key: str
    label: str
    plural: str
    icon: str
    group: str
    description: str
    new_label: str = ""       # « Nouveau cours », « Nouvelle certification »…
    manager_only: bool = False

    @property
    def list_url(self):
        return reverse("studio:list", args=[self.key])

    @property
    def new_url(self):
        return reverse("studio:create", args=[self.key])


GROUPS = [
    (
        "catalogue",
        "Catalogue ANSSI",
        "Les documents de référence que les apprenants lisent et suivent.",
    ),
    (
        "certifications",
        "Certifications et parcours",
        "Les objectifs des apprenants et les chemins pour y arriver.",
    ),
    ("apprentissage", "Apprendre", "Les contenus pédagogiques construits à partir des guides."),
    ("audio", "Audio", "Écouter les guides."),
]

TYPES = [
    ContentType(
        "guide",
        "Guide",
        "Guides",
        "📘",
        "catalogue",
        "Un document de l'ANSSI (ou équivalent) : fiche, classement et PDF à lire.",
        new_label="Nouveau guide",
    ),
    ContentType(
        "domaine",
        "Domaine",
        "Domaines",
        "🗂️",
        "catalogue",
        "Un grand thème de la cybersécurité, qui regroupe des guides et forme un parcours.",
        new_label="Nouveau domaine",
        manager_only=True,
    ),
    ContentType(
        "referentiel",
        "Référentiel",
        "Référentiels",
        "📐",
        "catalogue",
        "Un référentiel d'exigences : PDF, grille à suivre et documents qu'il cite.",
        new_label="Nouveau référentiel",
    ),
    ContentType(
        "programme",
        "Programme d'examen",
        "Programmes d'examen",
        "🎯",
        "catalogue",
        "Les domaines (et leur poids) du programme d'une certification professionnelle."
        " Reprenez-les brièvement, avec le lien officiel : le texte détaillé appartient au"
        " détenteur de la certification.",
        new_label="Nouveau programme d'examen",
    ),
    ContentType(
        "certification",
        "Certification",
        "Certifications",
        "🏅",
        "certifications",
        "Une certification ou qualification : public, prérequis, examen, référentiels liés.",
        new_label="Nouvelle certification",
    ),
    ContentType(
        "parcours",
        "Parcours sur mesure",
        "Parcours sur mesure",
        "🧭",
        "certifications",
        "Des guides choisis, dans l'ordre choisi, avec un mot à chaque étape.",
        new_label="Nouveau parcours",
    ),
    ContentType(
        "cours",
        "Cours",
        "Cours",
        "🎓",
        "apprentissage",
        "Des leçons courtes avec un quiz chacune, puis un examen final.",
        new_label="Nouveau cours",
    ),
    ContentType(
        "podcast",
        "Podcast",
        "Podcasts",
        "🎙️",
        "apprentissage",
        "Une série d'épisodes en dialogue entre Clara et Hugo, enregistrés à deux voix.",
        new_label="Nouveau podcast",
    ),
    ContentType(
        "qcm",
        "QCM d'un guide",
        "QCM des guides",
        "🧠",
        "apprentissage",
        "Les 6 séries de questions du test de connaissances d'un guide.",
        new_label="Nouveau QCM",
    ),
    ContentType(
        "lexique",
        "Lexique d'un guide",
        "Lexiques",
        "📖",
        "apprentissage",
        "Les sigles et termes d'un guide, expliqués pendant la lecture.",
        new_label="Nouveau lexique",
    ),
    ContentType(
        "audio",
        "Version audio enregistrée",
        "Versions audio enregistrées",
        "🎧",
        "audio",
        "Votre propre enregistrement d'un guide, chapitre par chapitre, à la place de la voix de"
        " synthèse.",
        new_label="Nouvel enregistrement",
    ),
]

BY_KEY = {t.key: t for t in TYPES}


# Types qui ne se publient pas sans l'IA locale : une série de podcasts est enregistrée par la
# synthèse vocale.
NEEDS_AI = {"podcast"}


def visible_types(user):
    return [t for t in TYPES if (not t.manager_only or user.is_content_manager)
            and (settings.AI_ENABLED or t.key not in NEEDS_AI)]
