from django.urls import path

from . import audio_views, catalogue_views, qcm_views, views

app_name = "studio"

urlpatterns = [
    path("", views.home, name="home"),
    # Aide de l'IA : préfixe « ia/ » (réponses longues, au fil de l'eau : réglage dédié dans nginx)
    path("ia/aide/", views.ai_stream, name="ai_stream"),
    path("ia/cours/<int:pk>/questions/", views.ai_questions, name="ai_questions"),
    path("ia/episode/<int:pk>/verifier/", views.ai_check_episode, name="ai_check_episode"),
    path("ia/qcm/<int:pk>/", qcm_views.qcm_ai, name="qcm_ai"),
    # QCM d'un guide
    path("qcm/<int:pk>/", qcm_views.qcm_edit, name="qcm_edit"),
    path("qcm/<int:pk>/question/", qcm_views.qcm_question, name="qcm_question"),
    path("qcm/<int:pk>/action/", qcm_views.qcm_action, name="qcm_action"),
    # Version audio enregistrée
    path("audio/<int:pk>/", audio_views.audio_edit, name="audio_edit"),
    path("audio/<int:pk>/chapitre/", audio_views.audio_part, name="audio_part"),
    path("audio/<int:pk>/action/", audio_views.audio_action, name="audio_action"),
    path("audio/<int:pk>/chapitre/<int:part>.mp3", audio_views.audio_file, name="audio_file"),
    # Contenus du catalogue (guide, domaine, référentiel, programme, certification, parcours)
    path("guide/<int:pk>/document/", catalogue_views.guide_document, name="guide_document"),
    path("referentiel/<int:pk>/cites/", catalogue_views.citations_save, name="citations_save"),
    path("parcours/<int:pk>/etape/", catalogue_views.path_step, name="path_step"),
    path("domaine/<int:pk>/contenu/", catalogue_views.domain_members, name="domain_members"),
    path("<str:key>/<int:pk>/grille/", catalogue_views.grid_save, name="grid_save"),
    path("<str:key>/nouveau/edition/", catalogue_views.editor, name="edit_new"),
    path("<str:key>/<int:pk>/", catalogue_views.editor, name="edit"),
    path("<str:key>/<int:pk>/action/", catalogue_views.action, name="action"),
    # Listes et ajout, pour tous les types
    path("<str:key>/", views.type_list, name="list"),
    path("<str:key>/ajouter/", views.create, name="create"),
]
