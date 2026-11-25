from django.urls import path

from . import review_views, views
from .models import Referential

app_name = "catalogue"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("recherche/", views.search, name="search"),
    path("lecture/", views.reading_list, name="reading_list"),
    path("lecture/ajouter/", views.reading_add, name="reading_add"),
    path("lecture/lu/", views.reading_finish, name="reading_finish"),
    path("lecture/<int:pk>/", views.reading_item, name="reading_item"),
    path("planning/", views.study_plan, name="study_plan"),
    path("planning/agenda.ics", views.study_plan_ics, name="study_plan_ics"),
    path("tests/", views.tests_overview, name="tests_overview"),
    path("tests/revision-du-jour/", views.daily_review, name="daily_review"),
    path("tests/revision-du-jour/<int:number>/", views.daily_review, name="daily_review_number"),
    path(
        "tests/revision-du-jour/acheter-seconde/", views.buy_second_review, name="buy_second_review"
    ),
    path("tests/<slug:slug>/serie-<int:number>/", views.quiz_take, name="quiz_take"),
    path("parcours/", views.path_list, name="path_list"),
    path("parcours/sur-mesure/<slug:slug>/", views.custom_path, name="custom_path"),
    path("parcours/sur-mesure/<slug:slug>/ajouter/", views.custom_path_add, name="custom_path_add"),
    path("parcours/<slug:slug>/", views.path_detail, name="path_detail"),
    path("parcours/<slug:slug>/ajouter/", views.reading_add_path, name="reading_add_path"),
    path("relecture/", review_views.review_list, name="review_list"),
    path("generations/", review_views.generation_dashboard, name="generation_dashboard"),
    path("generations/<str:kind>/", review_views.generation_action, name="generation_action"),
    path("generations/<str:kind>/file/", review_views.generation_queue, name="generation_queue"),
    path(
        "generations/<str:kind>/file/action/",
        review_views.generation_queue_action,
        name="generation_queue_action",
    ),
    path("relecture/<int:pk>/", review_views.review_detail, name="review_detail"),
    path("relecture/lexique/<int:pk>/", review_views.glossary_review, name="glossary_review"),
    path(
        "relecture/lexique/<int:pk>/action/",
        review_views.glossary_review_action,
        name="glossary_review_action",
    ),
    path("relecture/lexique/entree/<int:pk>/", review_views.glossary_entry, name="glossary_entry"),
    path("relecture/<int:pk>/action/", review_views.review_draft, name="review_draft"),
    path("relecture/question/<int:pk>/", review_views.review_question, name="review_question"),
    path("relecture/demander/<slug:slug>/", review_views.review_request, name="review_request"),
    path(
        "relecture/demander-referentiel/<slug:slug>/",
        review_views.review_request,
        {"model": Referential},
        name="review_request_referential",
    ),
    path("guides/", views.guide_list, name="guide_list"),
    path("guides/<slug:slug>/", views.guide_detail, name="guide_detail"),
    path("guides/<slug:slug>/illustration/", views.guide_cover, name="guide_cover"),
    path("guides/<slug:slug>/ecouter/", views.guide_listen, name="guide_listen"),
    path("guides/<slug:slug>/question/", views.guide_ask, name="guide_ask"),
    path("assistant/question/", views.guide_ask, name="assistant_ask"),
    path("guides/<slug:slug>/ecouter/progression/", views.listen_progress, name="listen_progress"),
    path(
        "guides/<slug:slug>/ecouter/<int:position>.mp3",
        views.narration_audio,
        name="narration_audio",
    ),
    path("guides/<slug:slug>/lire/<int:document_id>/", views.guide_read, name="guide_read"),
    path(
        "guides/<slug:slug>/document/<int:document_id>/",
        views.guide_document,
        name="guide_document",
    ),
    path("referentiels/", views.referential_list, name="referential_list"),
    path("referentiels/<slug:slug>/", views.referential_detail, name="referential_detail"),
    path("referentiels/<slug:slug>/lire/", views.referential_read, name="referential_read"),
    path(
        "referentiels/<slug:slug>/serie-<int:number>/",
        views.referential_quiz_take,
        name="referential_quiz_take",
    ),
    path(
        "referentiels/<slug:slug>/document/",
        views.referential_document,
        name="referential_document",
    ),
    path("referentiels/<slug:slug>/trame.xlsx", views.referential_trame, name="referential_trame"),
    path(
        "referentiels/<slug:slug>/grille.xlsx", views.referential_export, name="referential_export"
    ),
    path("exigences/<int:pk>/statut/", views.requirement_status, name="requirement_status"),
    path("certifications/", views.certification_list, name="certification_list"),
    path("programmes-examen/", views.exam_list, name="exam_list"),
    path("lexique/", views.glossary_index, name="glossary_index"),
    path("certifications/<slug:slug>/", views.certification_detail, name="certification_detail"),
    path(
        "certifications/<slug:slug>/ajouter-guides/",
        views.reading_add_certification,
        name="reading_add_certification",
    ),
]
