from django.contrib import admin

from .models import (
    Certification,
    ContentImport,
    DailyReview,
    DailyReviewAttempt,
    Guide,
    GuideCategory,
    GuideCitation,
    GuideDocument,
    Narration,
    NarrationSection,
    NewsItem,
    QuizDraft,
    QuizAttempt,
    QuizQuestion,
    ReadingListItem,
    Referential,
    Requirement,
    StudyPlan,
)

@admin.register(GuideCategory)
class GuideCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "position", "slug")
    list_editable = ("position",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


class GuideDocumentInline(admin.TabularInline):
    model = GuideDocument
    extra = 0
    fields = ("label", "file", "source_url", "page_count", "position")


@admin.register(Guide)
class GuideAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "level", "estimated_minutes", "language", "published_on",
                    "is_published")
    list_filter = ("is_published", "language", "level", "category")
    list_editable = ("level", "estimated_minutes")
    search_fields = ("title", "summary", "theme", "anssi_id")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "published_on"
    readonly_fields = ("anssi_id", "created_at", "updated_at")
    inlines = [GuideDocumentInline]


@admin.register(StudyPlan)
class StudyPlanAdmin(admin.ModelAdmin):
    list_display = ("user", "minutes_per_day", "updated_at")
    readonly_fields = ("user", "minutes_per_day", "updated_at")

    def has_add_permission(self, request):
        return False


class RequirementInline(admin.TabularInline):
    model = Requirement
    fk_name = "referential"
    extra = 0
    fields = ("position", "kind", "ref", "theme", "text", "weight")
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).filter(parent__isnull=True)


class GuideCitationInline(admin.TabularInline):
    model = GuideCitation
    extra = 0
    fields = ("position", "code", "title", "guide")
    autocomplete_fields = ("guide",)


@admin.register(Referential)
class ReferentialAdmin(admin.ModelAdmin):
    list_display = ("name", "short_name", "kind", "version", "issuer", "is_published")
    list_filter = ("kind", "is_published")
    search_fields = ("name", "short_name", "description")
    prepopulated_fields = {"slug": ("short_name",)}
    inlines = [GuideCitationInline, RequirementInline]


@admin.register(Requirement)
class RequirementAdmin(admin.ModelAdmin):
    list_display = ("ref", "kind", "referential", "theme")
    list_filter = ("kind", "referential")
    search_fields = ("ref", "text", "theme")
    raw_id_fields = ("parent",)


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = ("name", "acronym", "kind", "issuer", "level", "is_published")
    list_filter = ("kind", "level", "is_published")
    search_fields = ("name", "acronym", "description", "issuer")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("referentials", "guide_categories")


@admin.register(ReadingListItem)
class ReadingListItemAdmin(admin.ModelAdmin):
    list_display = ("user", "document", "added_at", "started_at", "finished_at")
    list_filter = ("finished_at",)
    search_fields = ("user__username", "guide__title", "referential__name")
    date_hierarchy = "added_at"
    # La lecture appartient à l'utilisateur : consultable, jamais modifiable à la main.
    readonly_fields = (
        "user",
        "guide",
        "referential",
        "position",
        "added_at",
        "started_at",
        "finished_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("subject", "series", "position", "text")
    list_filter = ("series", "guide__category")
    search_fields = ("text", "guide__title", "referential__name")
    autocomplete_fields = ("guide",)


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "subject", "series", "score", "total", "created_at")
    list_filter = ("series",)
    search_fields = ("user__username", "guide__title", "referential__name")
    readonly_fields = (
        "user",
        "guide",
        "referential",
        "series",
        "score",
        "total",
        "answers",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class DailyReviewAttemptInline(admin.TabularInline):
    model = DailyReviewAttempt
    extra = 0
    can_delete = False
    readonly_fields = ("score", "total", "answers", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DailyReview)
class DailyReviewAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "created_at")
    search_fields = ("user__username",)
    readonly_fields = ("user", "date", "question_ids", "created_at")
    inlines = [DailyReviewAttemptInline]

    def has_add_permission(self, request):
        return False


@admin.register(NewsItem)
class NewsItemAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "kind", "confirmed", "published_at")
    list_filter = ("kind", "source", "confirmed")
    search_fields = ("title", "summary")
    readonly_fields = ("source", "kind", "guid", "fetched_at")


class NarrationSectionInline(admin.TabularInline):
    model = NarrationSection
    extra = 0
    can_delete = False
    fields = ("position", "title", "duration_seconds", "audio")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Narration)
class NarrationAdmin(admin.ModelAdmin):
    list_display = (
        "guide",
        "status",
        "sections_done",
        "sections_total",
        "duration_seconds",
        "requested_at",
        "finished_at",
    )
    list_filter = ("status", "priority")
    search_fields = ("guide__title",)
    readonly_fields = (
        "guide",
        "status",
        "voice",
        "version",
        "sections_total",
        "sections_done",
        "duration_seconds",
        "error",
        "requested_by",
        "requested_at",
        "started_at",
        "finished_at",
    )
    inlines = [NarrationSectionInline]
    actions = ["requeue"]

    @admin.action(description="Remettre en file (régénérer l'audio)")
    def requeue(self, request, queryset):
        count = queryset.exclude(status=Narration.Status.PROCESSING).update(
            status=Narration.Status.PENDING, priority=0, error="")
        self.message_user(request, f"{count} version(s) audio remise(s) en file.")


@admin.register(QuizDraft)
class QuizDraftAdmin(admin.ModelAdmin):
    """Suivi des générations ; la relecture se fait dans l'application (menu du compte → Relecture
    des QCM)."""
    list_display = (
        "subject",
        "status",
        "llm_model",
        "requested_at",
        "generated_at",
        "published_at",
    )
    list_filter = ("status", "llm_model")
    search_fields = ("guide__title", "referential__name")
    readonly_fields = (
        "guide",
        "referential",
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



@admin.register(ContentImport)
class ContentImportAdmin(admin.ModelAdmin):
    """Historique des packs de contenus importés (lecture seule)."""
    list_display = ("version", "origin", "built_at", "imported_at", "forced")
    readonly_fields = ("version", "origin", "built_at", "imported_at", "forced", "report")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
