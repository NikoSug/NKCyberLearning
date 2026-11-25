"""
Écriture et relecture des podcasts (générés par l'IA ou écrits dans l'atelier de création).

Un épisode par page (script en dialogue, modifiable). Le rédacteur écrit ses propres séries « en
rédaction » puis les soumet ; le gestionnaire de contenu relit, renvoie au rédacteur, ou valide :
« Valider et enregistrer » envoie la série au service podcastgen, qui la publie une fois
enregistrée.
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from studio import ai, sources
from studio.permissions import author_required, can_edit, check_view, is_mine

from . import podcastgen
from .models import HOSTS, Episode, Series

Status = Series.Status


def _back_url(user, series):
    if series.author_id or not user.is_content_manager:
        return sources.list_url("podcast", series.status)
    return reverse("catalogue:review_list") + "?type=podcast"


def _hint(user, series, problems):
    todo = f" À faire avant : {' ; '.join(problems)}." if problems else ""
    if series.status == Status.WRITING:
        return (
            "Brouillon : validez quand c'est prêt, les deux voix enregistreront la série."
            if user.is_content_manager
            else (
                "Brouillon : soumettez quand c'est prêt, un gestionnaire relira puis fera"
                " enregistrer."
            )
        ) + todo
    if series.status == Status.REVIEW:
        return (
            "À relire : validez (enregistrement à deux voix puis publication)"
            + (", ou renvoyez au rédacteur." if series.author_id else ".")
            + todo
            if user.is_content_manager
            else "En relecture : plus modifiable, sauf si elle vous est renvoyée."
        )
    if series.status == Status.PUBLISHED:
        count = len(podcastgen.to_rerecord(series))
        return (
            "Publiée : titres et présentations changent tout de suite ; un texte d'épisode modifié"
            " demande un nouvel enregistrement"
            + (f" ({count} épisode(s) à réenregistrer)." if count else ".")
        )
    if series.status in (Status.TO_RECORD, Status.RECORDING):
        return "Validée : les deux voix l'enregistrent à leur tour, puis elle sera publiée."
    if series.status in (Status.PENDING, Status.GENERATING):
        return "L'IA écrit la série à son tour (générations en arrière-plan)."
    return ""


def _episode_url(series, position):
    return reverse("podcasts:review_series", args=[series.pk]) + f"?episode={position}"


@author_required
def review_series(request, pk):
    series = get_object_or_404(Series.objects.select_related("category", "author"), pk=pk)
    user = request.user
    check_view(user, series)
    episodes = list(series.episodes.all())
    episode = next((e for e in episodes if str(e.position) == request.GET.get("episode")), None) \
        or (episodes[0] if episodes else None)
    index = episodes.index(episode) if episode else 0
    for item in episodes:
        item.problems = podcastgen.script_problems(item.script)
    return render(
        request,
        "podcasts/review_series.html",
        {
            "series": series,
            "episodes": episodes,
            "episode": episode,
            "previous": episodes[index - 1] if episode and index else None,
            "next": episodes[index + 1] if episode and index + 1 < len(episodes) else None,
            "script": podcastgen.script_text(episode.script) if episode else "",
            "problems": podcastgen.review_problems(series),
            "editable": can_edit(user, series),
            "writing": series.status == Status.WRITING,
            "mine": is_mine(user, series),
            "manager": user.is_content_manager,
            "back_url": _back_url(user, series),
            "back_label": (
                "Podcasts" if series.author_id or not user.is_content_manager else "Relecture"
            ),
            "hint": _hint(
                user,
                series,
                (
                    podcastgen.review_problems(series)
                    if can_edit(user, series) and series.status != Status.PUBLISHED
                    else []
                ),
            ),
            "to_rerecord": (
                len(podcastgen.to_rerecord(series)) if series.status == Status.PUBLISHED else 0
            ),
            "hosts": HOSTS,
            "min_turns": podcastgen.MIN_TURNS,
            "min_words": podcastgen.MIN_WORDS,
            "ai_actions": [
                (key, label) for key, (label, _i, _c) in ai.ACTIONS.items() if key != "verifier"
            ],
            "ai_busy": ai.available(),
        },
    )


@author_required
@require_POST
def review_series_action(request, pk):
    series = get_object_or_404(Series, pk=pk)
    user = request.user
    check_view(user, series)
    action = request.POST.get("action")
    back = _back_url(user, series)
    manager = user.is_content_manager
    if action == "relancer" and (
        manager
        and series.status in (Status.FAILED, Status.REVIEW, Status.DISCARDED)
        or can_edit(user, series)
        and series.author_id
        and series.status != Status.PUBLISHED
    ):
        podcastgen.reset(series, user)  # une série de rédacteur garde son auteur et ses consignes
        messages.success(request, "Série remise en file : l'IA la réécrira entièrement" +
                         (", puis elle reviendra dans l'atelier." if series.author_id else "."))
        return redirect(back)
    if action == "renvoyer" and manager and series.status == Status.REVIEW and series.author_id:
        series.status = Status.WRITING
        series.save(update_fields=["status"])
        messages.info(
            request, f"« {series.title} » est renvoyée à {series.author.username}, en rédaction."
        )
        return redirect(back)
    if not can_edit(user, series):
        messages.error(request, "Cette série n'est pas modifiable en ce moment.")
        return redirect(back)
    if action == "enregistrer":
        title = request.POST.get("title", "").strip()
        if len(title) < 5:
            messages.error(request, "Le titre est trop court.")
        else:
            series.title, series.summary = title[:200], request.POST.get("summary", "").strip()
            series.brief = request.POST.get("brief", series.brief).strip()[:3000]
            series.save(update_fields=["title", "summary", "brief"])
            messages.success(request, "Présentation de la série enregistrée.")
        return redirect(_episode_url(series, request.POST.get("episode", "1")))
    if action == "ajouter-episode":
        position = (series.episodes.aggregate(n=Max("position"))["n"] or 0) + 1
        Episode.objects.create(
            series=series, position=position, title=f"Épisode {position}", script=[]
        )
        messages.success(
            request,
            f"Épisode {position} ajouté : donnez-lui un titre, puis écrivez le dialogue (ou "
            "demandez un brouillon à l'IA).",
        )
        return redirect(_episode_url(series, position))
    if action == "soumettre" and series.status == Status.WRITING:
        problems = podcastgen.review_problems(series)
        if problems:
            messages.error(request, "Avant de soumettre : " + " ; ".join(problems) + ".")
            return redirect(_episode_url(series, request.POST.get("episode", "1")))
        series.status = Status.REVIEW
        series.save(update_fields=["status"])
        messages.success(
            request,
            f"« {series.title} » est soumise à la relecture : un gestionnaire de contenu la "
            "validera ou vous la renverra avec ses remarques.",
        )
        return redirect(back)
    if action == "reenregistrer" and manager and series.status == Status.PUBLISHED:
        count = len(podcastgen.to_rerecord(series))
        if not count:
            messages.info(request, "Aucun épisode n'a changé depuis l'enregistrement.")
            return redirect(_episode_url(series, request.POST.get("episode", "1")))
        podcastgen.rerecord(series, user)
        messages.success(
            request,
            f"{count} épisode(s) seront réenregistrés à leur tour ; la série reste écoutable en "
            "attendant (ancienne version des épisodes modifiés).",
        )
        return redirect(_episode_url(series, request.POST.get("episode", "1")))
    if action == "valider" and manager:
        try:
            podcastgen.approve(series, user)
        except podcastgen.GenerationError as exc:
            messages.error(request, str(exc))
            return redirect(_episode_url(series, request.POST.get("episode", "1")))
        messages.success(
            request,
            f"« {series.title} » est validée : l'enregistrement à deux voix est en file, la série "
            "sera publiée dès qu'il sera terminé.",
        )
        return redirect(back)
    if action == "abandonner" and manager and series.status == Status.REVIEW:
        series.status = Status.DISCARDED
        series.save(update_fields=["status"])
        messages.info(request, "Série abandonnée.")
        return redirect(back)
    if (
        action == "supprimer"
        and series.status == Status.WRITING
        and (is_mine(user, series) or manager)
    ):
        title = series.title
        for episode in series.episodes.all():
            episode.audio.delete(save=False)
        series.delete()
        messages.info(request, f"« {title} » supprimée.")
        return redirect("studio:list", "podcast")
    messages.error(request, "Action inconnue.")
    return redirect(_episode_url(series, "1"))


@author_required
@require_POST
def review_episode(request, pk):
    episode = get_object_or_404(Episode.objects.select_related("series"), pk=pk)
    series = episode.series
    check_view(request.user, series)
    if not can_edit(request.user, series):
        messages.error(request, "Cette série n'est pas modifiable en ce moment.")
        return redirect(_episode_url(series, episode.position))
    action = request.POST.get("action")
    if action == "reecrire":
        podcastgen.rewrite_episode(episode, request.user)
        messages.success(
            request,
            f"L'épisode {episode.position} sera réécrit par l'IA (les autres épisodes sont "
            "gardés) ; la série reviendra ensuite "
            + ("dans l'atelier." if series.author_id else "en relecture."),
        )
        return redirect(_back_url(request.user, series))
    if action == "supprimer":
        with transaction.atomic():
            episode.audio.delete(save=False)
            episode.delete()
            for n, item in enumerate(
                series.episodes.order_by("position"), start=1
            ):  # positions sans trou
                if item.position != n:
                    Episode.objects.filter(pk=item.pk).update(position=n)
        messages.info(request, f"Épisode « {episode.title} » supprimé.")
        return redirect(_episode_url(series, max(episode.position - 1, 1)))
    title = request.POST.get("title", "").strip()
    try:
        script = podcastgen.parse_script(request.POST.get("body", ""))
    except podcastgen.GenerationError as exc:
        messages.error(request, f"Script illisible : {exc}.")
        return redirect(_episode_url(series, episode.position))
    if len(title) < 3:
        messages.error(request, "Épisode non enregistré : titre trop court.")
        return redirect(_episode_url(series, episode.position))
    problems = podcastgen.script_problems(script)
    if (
        problems and series.status != Status.WRITING
    ):  # en relecture, seul un épisode complet est accepté
        messages.error(request, "Épisode non enregistré : " + " ; ".join(problems) + ".")
        return redirect(_episode_url(series, episode.position))
    if series.status == Status.PUBLISHED and script != episode.script:
        episode.needs_recording = True  # l'ancien audio reste écouté jusqu'au réenregistrement
    episode.title, episode.script, episode.flags = (
        title[:200],
        script,
        [],
    )  # relu : remarques traitées
    episode.summary = request.POST.get("summary", "").strip()[:400]
    episode.save(update_fields=["title", "script", "summary", "flags", "needs_recording"])
    note = f" Encore à compléter avant de soumettre : {' ; '.join(problems)}." if problems else ""
    messages.success(request, f"Épisode {episode.position} enregistré.{note}")
    return redirect(_episode_url(series, episode.position))
