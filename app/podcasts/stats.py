"""Statistiques de la génération des podcasts pour le tableau de bord des générations (mêmes clés
que les cours)."""

from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from catalogue import generation

from . import podcastgen
from .models import Series

Status = Series.Status
WORKING = (Status.GENERATING, Status.RECORDING)
WAITING = (Status.PENDING, Status.TO_RECORD)


def _average(values):
    values = [v for v in values if v and v > 0]
    return sum(values) / len(values) if values else None


def pace(series, waiting, to_record, running):
    """
    Une série passe par deux étapes (écriture du script par l'IA, puis enregistrement à deux voix) :
    sa durée est la somme des deux, et une série à enregistrer ne coûte plus que l'enregistrement.
    """
    recent = generation.RECENT_FOR_PACE
    scripts = list(series.filter(script_seconds__isnull=False).order_by("-generated_at")
                   .values_list("script_seconds", flat=True)[:recent])
    recordings = list(series.filter(recording_seconds__isnull=False).order_by("-published_at")
                      .values_list("recording_seconds", flat=True)[:recent])
    script, recording = _average(scripts), _average(recordings)
    average = (script or 0) + (recording or 0) or None
    # Anciennes séries : seule l'une des deux durées a pu être reprise, on compte ce qui est connu.
    done = (
        series.filter(Q(script_seconds__isnull=False) | Q(recording_seconds__isnull=False))
        .exclude(status=Status.FAILED)
        .exclude(generated_at__isnull=True, published_at__isnull=True)
    )
    latest = sorted(
        done.values_list("generated_at", "published_at", "script_seconds", "recording_seconds"),
        key=lambda row: max(d for d in row[:2] if d),
        reverse=True,
    )[:recent]
    queue = (waiting - to_record) * (average or 0) + to_record * (recording or average or 0)
    result = generation._pace(
        [timedelta(seconds=(s or 0) + (r or 0)) for _g, _p, s, r in latest],
        waiting,
        running,
        average=average,
        waiting_seconds=queue,
    )
    result["detail"] = " · ".join(
        part
        for part in [
            f"écriture ≈ {generation.human_duration(timedelta(seconds=script))}" if script else "",
            (
                f"enregistrement ≈ {generation.human_duration(timedelta(seconds=recording))}"
                if recording
                else ""
            ),
        ]
        if part
    )
    return result


def podcast_stats():
    service = generation._service(generation.Kind.PODCAST)
    series = Series.objects.select_related("category")
    counts = dict(series.values_list("status").annotate(n=Count("pk")))
    failed = series.filter(status=Status.FAILED)
    waiting = sum(counts.get(s, 0) for s in WAITING)
    running = generation._running(series.filter(status__in=WORKING).order_by("started_at"))
    return {
        **service,
        "eligible": generation.eligible_guides().values("category").distinct().count(),
        "covered": counts.get(Status.PUBLISHED, 0),
        "counts": {status: counts.get(status, 0) for status in Status.values},
        "running": running,
        "next": series.filter(status__in=WAITING).order_by(*Series.QUEUE_ORDER)[:5],
        "pending": waiting,
        "failed": failed.order_by("-requested_at")[:8],
        "failed_count": failed.count(),
        "missing": podcastgen.themes_without_series().count(),
        "pace": pace(Series.objects.all(), waiting, counts.get(Status.TO_RECORD, 0), running),
        "chart": generation._chart(
            Series.objects.filter(generated_at__isnull=False)
            .annotate(when=F("generated_at"))
            .filter(
                when__date__gte=timezone.localdate() - timedelta(days=generation.CHART_DAYS - 1)
            )
        ),
    }
