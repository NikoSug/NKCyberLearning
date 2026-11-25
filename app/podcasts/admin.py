from django.contrib import admin

from .models import Episode, PodcastProgress, Series


class EpisodeInline(admin.TabularInline):
    model = Episode
    fields = ("position", "title", "duration_seconds")
    readonly_fields = ("duration_seconds",)
    extra = 0
    show_change_link = True


@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):
    """Suivi des séries ; la relecture se fait dans l'application (Relecture → onglet Podcasts)."""
    list_display = ("title", "category", "status", "requested_at", "generated_at", "published_at")
    list_filter = ("status", "category")
    search_fields = ("title", "category__name")
    readonly_fields = (
        "plan",
        "llm_model",
        "voice",
        "progress",
        "error",
        "requested_by",
        "requested_at",
        "started_at",
        "generated_at",
        "approved_by",
        "approved_at",
        "published_at",
        "duration_seconds",
    )
    inlines = [EpisodeInline]


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ("series", "position", "title", "duration_seconds")
    search_fields = ("title", "series__title")


@admin.register(PodcastProgress)
class PodcastProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "series", "episode", "completed_at")
    readonly_fields = ("user", "series", "episode", "seconds", "episodes_done", "completed_at")
