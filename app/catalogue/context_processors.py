from . import generation


def assistant(request):
    """Le bouton de l'assistant est masqué quand l'IA est coupée (mode jeu)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    return {"gaming_mode": generation.gaming()}


def review(request):
    """Nombre d'éléments générés à relire (QCM, cours, lexiques), pour le menu des gestionnaires de
    contenu."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_content_manager:
        return {}
    from .review_views import review_counts  # import tardif : les vues importent les cours
    return {"quiz_reviews": review_counts()["tout"]}


# Rubrique de la barre du haut pour chaque page : (espace de noms, nom d'URL) ou espace de noms
# entier.
NAV_SECTIONS = {
    "accueil": {("catalogue", "dashboard")},
    "cours": {"courses"},
    "podcasts": {"podcasts"},
    "catalogue": {
        ("catalogue", name)
        for name in (
            "search",
            "path_list",
            "path_detail",
            "custom_path",
            "guide_list",
            "guide_detail",
            "guide_read",
            "guide_listen",
            "referential_list",
            "referential_detail",
            "referential_read",
            "certification_list",
            "certification_detail",
            "exam_list",
            "glossary_index",
        )
    },
    "suivi": {
        ("catalogue", name)
        for name in (
            "tests_overview",
            "daily_review",
            "daily_review_number",
            "quiz_take",
            "reading_list",
            "study_plan",
        )
    },
    "gestion": (
        {
            "studio",
            ("accounts", "user_list"),
            ("courses", "review_list"),
            ("courses", "review_course"),
            ("courses", "review_lesson"),
            ("podcasts", "review_series"),
            ("podcasts", "review_episode"),
        }
        | {
            ("catalogue", name)
            for name in (
                "review_list",
                "review_detail",
                "review_draft",
                "generation_dashboard",
                "generation_queue",
                "glossary_review",
                "glossary_entry",
            )
        }
    ),
}


def navigation(request):
    """Rubrique active de la barre du haut (la gestion passe avant : ses pages relisent des cours,
    des podcasts…)."""
    match = getattr(request, "resolver_match", None)
    if not match:
        return {}
    key = (match.namespace, match.url_name)
    for section in ("gestion", "accueil", "cours", "podcasts", "catalogue", "suivi"):
        members = NAV_SECTIONS[section]
        if key in members or match.namespace in members:
            return {"nav_section": section}
    return {"nav_section": ""}
