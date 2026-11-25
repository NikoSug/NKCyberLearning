from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalogue.views import _serve_range
from progression.models import XPEvent
from progression.xp import Award, grant

from .models import Episode, PodcastProgress, Series

EPISODE_XP = 10
SERIES_XP = 20


def published():
    """Séries écoutables : publiées, ou en cours de réenregistrement après une modification."""
    Status = Series.Status
    return Series.objects.filter(
        Q(status=Status.PUBLISHED)
        | Q(status__in=[Status.TO_RECORD, Status.RECORDING], published_at__isnull=False)
    )


@login_required
def podcast_list(request):
    series = list(published().select_related("category").annotate(episode_count=Count("episodes")))
    progress = {
        p.series_id: p for p in PodcastProgress.objects.filter(user=request.user, series__in=series)
    }
    for item in series:
        mine = progress.get(item.pk)
        item.listened = len(mine.episodes_done) if mine else 0
        item.started = mine is not None
    return render(
        request,
        "podcasts/podcast_list.html",
        {
            "series": series,
            "in_progress": [s for s in series if s.started and s.listened < s.episode_count][:4],
        },
    )


@login_required
def series_detail(request, slug):
    series = get_object_or_404(published().select_related("category"), slug=slug)
    progress = PodcastProgress.objects.filter(user=request.user, series=series).first()
    done = set(progress.episodes_done) if progress else set()
    episodes = list(series.episodes.all())
    for episode in episodes:
        episode.listened = episode.position in done
    return render(request, "podcasts/series_detail.html", {
        "series": series, "episodes": episodes, "progress": progress, "episode_xp": EPISODE_XP,
        "series_xp": SERIES_XP,
    })


@login_required
def episode_audio(request, slug, position):
    """Fichier MP3 d'un épisode, réservé aux utilisateurs connectés."""
    episode = get_object_or_404(
        Episode, series__in=published(), series__slug=slug, position=position
    )
    if not episode.audio:
        raise Http404("Audio indisponible.")
    return _serve_range(request, episode.audio, "audio/mpeg")


@login_required
@require_POST
def listen_progress(request, slug):
    """Position d'écoute (reprise) ; XP pour chaque épisode écouté jusqu'au bout, bonus pour la
    série entière."""
    series = get_object_or_404(published(), slug=slug)
    episodes = {e.position: e for e in series.episodes.all()}
    try:
        position = int(request.POST.get("section", ""))
        seconds = max(0.0, min(float(request.POST.get("seconds", "0")), 24 * 3600))
    except ValueError:
        return JsonResponse({"error": "paramètres invalides"}, status=400)
    if position not in episodes:
        return JsonResponse({"error": "épisode inconnu"}, status=400)

    progress, _ = PodcastProgress.objects.get_or_create(user=request.user, series=series)
    progress.episode, progress.seconds = position, seconds
    result = {}
    if request.POST.get("ended") == "1":
        awards = []
        if position not in progress.episodes_done:
            progress.episodes_done = sorted(set(progress.episodes_done) | {position})
            episode = episodes[position]
            awards.append(Award(f"podcast:{episode.pk}", EPISODE_XP, XPEvent.Kind.READING,
                                f"Podcast écouté : {episode.title}"))
        if not progress.completed_at and set(episodes) <= set(progress.episodes_done):
            progress.completed_at = timezone.now()
            awards.append(Award(f"podcast-serie:{series.pk}", SERIES_XP, XPEvent.Kind.READING,
                                f"Série écoutée : {series.title}"))
            result["completed"] = True
        gain = grant(request.user, awards) if awards else None
        listened = len(set(progress.episodes_done) & set(episodes))
        xp = f" (+{gain.xp} XP)" if gain and gain.xp else ""
        result["message"] = (f"Série écoutée en entier{xp}. Bravo !" if result.get("completed")
                             else f"{listened} épisode(s) écouté(s) sur {len(episodes)}{xp}.")
    progress.save()
    result.update(done=len(set(progress.episodes_done) & set(episodes)), total=len(episodes))
    return JsonResponse(result)
