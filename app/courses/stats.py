"""Statistiques de la génération des cours pour le tableau de bord des générations (mêmes clés que
les QCM)."""

from datetime import timedelta

from django.db.models import Count, F
from django.utils import timezone

from catalogue import generation

from . import coursegen
from .models import Course

Status = Course.Status


def course_stats():
    service = generation._service(generation.Kind.COURSE)
    courses = Course.objects.select_related("category", "guide")
    counts = dict(courses.values_list("status").annotate(n=Count("pk")))
    done = courses.filter(
        status__in=[Status.REVIEW, Status.PUBLISHED],
        started_at__isnull=False,
        generated_at__isnull=False,
    ).order_by("-generated_at")[: generation.RECENT_FOR_PACE]
    failed = courses.filter(status=Status.FAILED)
    themes, guides = (
        coursegen.themes_without_course().count(),
        coursegen.guides_without_course().count(),
    )
    eligible_themes = generation.eligible_guides().values("category").distinct().count()
    running = generation._running(courses.filter(status=Status.GENERATING).order_by("started_at"))
    return {
        **service,
        "eligible": eligible_themes + generation.eligible_guides().count(),
        "covered": counts.get(Status.PUBLISHED, 0),
        "counts": {status: counts.get(status, 0) for status in Status.values},
        "running": running,
        "next": courses.filter(status=Status.PENDING).order_by(*Course.QUEUE_ORDER)[:5],
        "pending": counts.get(Status.PENDING, 0),
        "failed": failed.order_by("-requested_at")[:8],
        "failed_count": failed.count(),
        "missing": themes + guides,
        "pace": generation._pace(
            [c.generated_at - c.started_at for c in done], counts.get(Status.PENDING, 0), running
        ),
        "chart": generation._chart(
            Course.objects.filter(generated_at__isnull=False)
            .annotate(when=F("generated_at"))
            .filter(
                when__date__gte=timezone.localdate() - timedelta(days=generation.CHART_DAYS - 1)
            )
        ),
    }
