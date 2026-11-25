from django.contrib import admin

from .models import Course, CourseQuestion, ExamAttempt, Lesson, LessonProgress


class LessonInline(admin.TabularInline):
    model = Lesson
    fields = ("position", "title", "minutes")
    extra = 0
    show_change_link = True


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    """Suivi des cours ; la relecture se fait dans l'application (menu du compte → Relecture des
    cours)."""
    list_display = (
        "title",
        "kind",
        "status",
        "level",
        "requested_at",
        "generated_at",
        "published_at",
    )
    list_filter = ("kind", "status", "level")
    search_fields = ("title", "guide__title", "category__name")
    readonly_fields = (
        "plan",
        "llm_model",
        "progress",
        "error",
        "requested_by",
        "requested_at",
        "started_at",
        "generated_at",
        "published_by",
        "published_at",
    )
    inlines = [LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("course", "position", "title", "minutes")
    search_fields = ("title", "course__title")


@admin.register(CourseQuestion)
class CourseQuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "course", "lesson")
    search_fields = ("text",)


@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "lesson", "best_score", "attempts", "passed_at")
    readonly_fields = ("user", "lesson", "best_score", "attempts", "passed_at")


@admin.register(ExamAttempt)
class ExamAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "course", "score", "total", "created_at")
    readonly_fields = ("user", "course", "score", "total", "answers", "created_at")
