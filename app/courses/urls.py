from django.urls import path

from . import review_views, views

app_name = "courses"

urlpatterns = [
    path("", views.course_list, name="course_list"),
    path("relecture/", review_views.review_list, name="review_list"),
    path("relecture/<int:pk>/", review_views.review_course, name="review_course"),
    path(
        "relecture/<int:pk>/action/", review_views.review_course_action, name="review_course_action"
    ),
    path("relecture/lecon/<int:pk>/", review_views.review_lesson, name="review_lesson"),
    path("relecture/question/<int:pk>/", review_views.review_question, name="review_question"),
    path("relecture/<int:pk>/question/", review_views.add_question, name="add_question"),
    path("<slug:slug>/", views.course_detail, name="course_detail"),
    path("<slug:slug>/lecon/<int:position>/", views.lesson, name="lesson"),
    path("<slug:slug>/lecon/<int:position>/quiz/", views.lesson_quiz, name="lesson_quiz"),
    path("<slug:slug>/lecon/<int:position>/repondre/", views.lesson_answer, name="lesson_answer"),
    path("<slug:slug>/examen/", views.exam, name="exam"),
]
