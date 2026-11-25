"""
Objets de la boutique (hors avatars, voir avatars.py), payés en émeraudes.

  - Cadres d'avatar, thèmes d'interface, titres de profil : achetés une fois,
    puis choisis librement (un seul équipé par catégorie). Un objet à 0 émeraude est
    offert : possédé d'office, sans achat (comme les avatars gratuits).
  - Bonus : consommés à l'usage (gel de série, potion double XP, seconde révision).

Chaque objet a un niveau minimum : la boutique s'agrandit avec la progression.
Comme pour les avatars, la clé est enregistrée en base : ne jamais la renommer.
"""
from dataclasses import dataclass

FRAME, THEME, TITLE, BONUS = "cadre", "theme", "titre", "bonus"
CATEGORIES = [
    (BONUS, "Bonus", "Des coups de pouce consommés à l'usage."),
    (FRAME, "Cadres d'avatar", "Une bordure autour de votre avatar, visible par vos amis."),
    (TITLE, "Titres de profil", "Affiché sous votre pseudo, dans le classement entre amis."),
    (THEME, "Thèmes d'interface", "Un nouvel habillage pour tout le site."),
]
COSMETICS = {FRAME, THEME, TITLE}


@dataclass(frozen=True)
class Item:
    key: str
    category: str
    name: str
    price: int
    min_level: int
    description: str = ""
    css: str = ""        # classe CSS (cadres) ou nom du thème
    stock_max: int = 0   # bonus stockables (gel de série)


# Bonus
STREAK_FREEZE = "gel-serie"
DOUBLE_XP = "double-xp"
SECOND_REVIEW = "seconde-revision"
DOUBLE_XP_MINUTES = 30

ITEMS = [
    Item(
        STREAK_FREEZE,
        BONUS,
        "Gel de série",
        30,
        2,
        "Protège votre série 🔥 un jour où vous n'apprenez pas. Utilisé automatiquement.",
        stock_max=2,
    ),
    Item(
        SECOND_REVIEW,
        BONUS,
        "Seconde révision",
        15,
        3,
        "Une deuxième révision aujourd'hui, avec de nouvelles questions.",
    ),
    Item(
        DOUBLE_XP,
        BONUS,
        "Potion double XP",
        40,
        4,
        f"Tous vos gains d'XP sont doublés pendant {DOUBLE_XP_MINUTES} minutes, dès l'achat.",
    ),
    Item("cadre-argent", FRAME, "Argent", 30, 2, css="frame-silver"),
    Item("cadre-or", FRAME, "Or", 60, 3, css="frame-gold"),
    Item("cadre-circuit", FRAME, "Circuit imprimé", 90, 4, css="frame-circuit"),
    Item("cadre-neon", FRAME, "Néon", 150, 5, css="frame-neon"),
    Item("cadre-prisme", FRAME, "Prisme", 250, 7, css="frame-prism"),
    Item("titre-pare-feu", TITLE, "Gardien du pare-feu", 25, 2),
    Item("titre-phishing", TITLE, "Chasseur de phishing", 25, 2),
    Item("titre-chiffrement", TITLE, "Pro du chiffrement", 50, 3),
    Item("titre-soc", TITLE, "Œil du SOC", 50, 4),
    Item("titre-hash", TITLE, "Briseur de hash", 80, 5),
    Item("titre-journaux", TITLE, "Maître des journaux", 80, 5),
    Item("titre-fantome", TITLE, "Fantôme du réseau", 150, 7),
    Item("titre-red-team", TITLE, "Légende de la Red Team", 250, 9),
    # Offerts : choisir entre clair et sombre (sans choix, le site suit le réglage de l'ordinateur).
    Item(
        "theme-clair",
        THEME,
        "Clair",
        0,
        1,
        "Le site en clair, même si votre ordinateur est réglé en mode sombre. Offert.",
        css="clair",
    ),
    Item(
        "theme-sombre",
        THEME,
        "Sombre",
        0,
        1,
        "Le site en sombre, même si votre ordinateur est réglé en mode clair. Offert.",
        css="sombre",
    ),
    Item(
        "theme-terminal", THEME, "Terminal", 80, 3, "Vert phosphore sur fond noir.", css="terminal"
    ),
    Item(
        "theme-audit",
        THEME,
        "Rapport d'audit",
        100,
        3,
        "Clair, papier et encre bleue.",
        css="audit",
    ),
    Item("theme-soc", THEME, "SOC de nuit", 120, 4, "Bleu nuit et alertes ambre.", css="soc"),
    Item("theme-neon", THEME, "Néon cyberpunk", 150, 5, "Rose et cyan électriques.", css="neon"),
]
BY_KEY = {item.key: item for item in ITEMS}


def get(key, category=None):
    item = BY_KEY.get(key)
    return item if item and (category is None or item.category == category) else None


def of(category):
    return [item for item in ITEMS if item.category == category]
