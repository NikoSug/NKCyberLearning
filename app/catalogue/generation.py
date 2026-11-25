"""
Pilotage des générations en arrière-plan : QCM par IA (service « quizgen ») et versions
audio (service « narrator »).

  - Pause : drapeau en base, lu par les services. Une génération en cours s'arrête au
    prochain point d'étape (entre deux séries de QCM, entre deux chapitres audio) et
    retourne dans la file ; le travail déjà fait est conservé et la reprise repart de là.
  - Activité : chaque service signale régulièrement qu'il tourne (dernière activité).
  - Coupe-circuit : après FAILURE_LIMIT échecs de suite (service d'IA arrêté, fichiers
    inaccessibles…), la génération se met en pause d'elle-même au lieu de vider la file en échecs.
  - Mode jeu : coupe d'un coup tout ce qui consomme (les deux générations, l'assistant) et
    décharge le modèle d'IA de la carte graphique ; à la sortie, tout reprend comme avant
    (les pauses posées séparément restent en place).
  - Statistiques du tableau de bord, identiques pour chaque type : faits sur le total, file, en
  cours,
    échecs, durée de génération (dernière, moyenne), temps écoulé de la tâche en cours, reste
    estimé.
"""

import json
import re
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import (
    GenerationControl,
    Guide,
    GuideDocument,
    Language,
    Narration,
    QuizDraft,
    QuizDraftQuestion,
    ServiceCommand,
)

Kind = GenerationControl.Kind
QuizStatus = QuizDraft.Status
AudioStatus = Narration.Status
ALIVE_AFTER = timedelta(
    minutes=5
)  # un chapitre audio long peut occuper le service quelques minutes
CHART_DAYS = 14
RECENT_FOR_PACE = 10                 # générations récentes prises pour estimer le rythme
FAILURE_LIMIT = 5


class Paused(Exception):
    """Levée au point d'étape d'une génération quand la pause est demandée."""


# --- Pause et activité ------------------------------------------------------------------------
def control(kind):
    return GenerationControl.objects.get_or_create(kind=kind)[0]


def is_paused(kind):
    """Pause de cette génération, ou mode jeu."""
    return GenerationControl.objects.filter(kind__in=[kind, Kind.GAMING], paused=True).exists()


def gaming():
    return GenerationControl.objects.filter(kind=Kind.GAMING, paused=True).exists()


def unload_model():
    """Libère la carte graphique tout de suite (au lieu d'attendre la fin du délai de maintien
    d'Ollama)."""
    for model in {
        settings.QUIZ_LLM_MODEL,
        settings.PREREVIEW_LLM_MODEL,
    }:  # générateur et pré-relecteur
        body = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
        request = urllib.request.Request(
            settings.QUIZ_LLM_URL.rstrip("/") + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15):  # nosec B310 (URL de configuration)
                pass
        except Exception:
            return False  # service d'IA arrêté : rien à libérer
    return True


VOICE_ACTIVE_FOR = timedelta(
    minutes=3
)  # sans signe de vie de la synthèse depuis, l'assistant reprend


def voice_busy(on=True):
    """La synthèse vocale occupe la carte graphique (ou vient de la libérer) : l'assistant se met en
    pause."""
    value = timezone.now() if on else None
    if not GenerationControl.objects.filter(kind=Kind.VOICE).update(last_seen=value):
        GenerationControl.objects.get_or_create(kind=Kind.VOICE, defaults={"last_seen": value})


def voice_active():
    item = GenerationControl.objects.filter(kind=Kind.VOICE).first()
    return bool(item and item.last_seen and timezone.now() - item.last_seen < VOICE_ACTIVE_FOR)


def set_gaming(on, user=None):
    """Active ou désactive le mode jeu ; à l'activation, le modèle est déchargé du GPU."""
    item = set_paused(Kind.GAMING, on, user)
    if on:
        unload_model()
    return item


def set_paused(kind, paused, user=None, note=""):
    item = control(kind)
    item.paused, item.note = paused, note if paused else ""
    item.paused_by, item.paused_at = (user, timezone.now()) if paused else (None, None)
    if not paused:
        item.failures = 0
    item.save(update_fields=["paused", "note", "paused_by", "paused_at", "failures"])
    return item


def record_success(kind):
    GenerationControl.objects.filter(kind=kind).exclude(failures=0).update(failures=0)


def record_failure(kind, error):
    """Compte un échec ; renvoie True si la génération vient d'être mise en pause (trop d'échecs de
    suite)."""
    control(kind)
    GenerationControl.objects.filter(kind=kind).update(failures=F("failures") + 1)
    item = control(kind)
    if item.failures >= FAILURE_LIMIT and not item.paused:
        set_paused(kind, True, note=f"Pause automatique après {item.failures} échecs de suite. "
                                    f"Dernière erreur : {str(error)[:180]}")
        return True
    return False


def heartbeat(kind, workers=None):
    """Le service signale qu'il tourne (et combien de générations il mène en parallèle)."""
    values = {"last_seen": timezone.now()}
    if workers:
        values["workers"] = workers
    if not GenerationControl.objects.filter(kind=kind).update(**values):
        GenerationControl.objects.get_or_create(kind=kind, defaults=values)


def checkpoint(kind):
    """Point d'étape d'une génération : signale l'activité et s'interrompt si la pause est
    demandée."""
    heartbeat(kind)
    if is_paused(kind):
        raise Paused


# --- Statistiques -------------------------------------------------------------------------------
def human_duration(value):
    """« 2 min 50 s », « 4 h 10 », « 1 j 3 h » (None → chaîne vide)."""
    if value is None:
        return ""
    seconds = max(int(value.total_seconds()), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days:
        return f"{days} j {hours} h"
    if hours:
        return f"{hours} h {minutes:02d}"
    return f"{minutes} min {seconds:02d} s" if minutes else f"{seconds} s"


STEP_RE = re.compile(r"(\d+)\s+sur\s+(\d+)")


def job_progress(item):
    """
    Avancement d'une génération, au même format pour tous les types : étape en cours (texte),
    étapes terminées et nombre total d'étapes (barre), durée écoulée.
    """
    label, done, total = (getattr(item, "progress", "") or ""), 0, 0
    if isinstance(item, Narration):
        total, done = item.sections_total, item.sections_done
        label = f"Chapitre {min(done + 1, total)} sur {total}" if total else "Extraction du texte"
    elif hasattr(
        item, "episodes"
    ):  # podcast : « Épisode 2 sur 4 », « Enregistrement : épisode 2 sur 4 »
        match = STEP_RE.search(label)
        if match:
            done, total = int(match.group(1)) - 1, int(match.group(2))
    elif hasattr(item, "plan"):  # cours : plan, leçons, examen
        lessons = item.lessons.count()
        total = len(item.plan) + 2 if item.plan else 0
        done = (1 + lessons) if item.plan else 0
    else:  # QCM (« Série 3 sur 6 »), lexique (« Partie 2 sur 4 »)
        match = STEP_RE.search(label)
        if match:
            done, total = int(match.group(1)) - 1, int(match.group(2))
    return {"label": label or "Démarrage", "done": done, "total": total,
            "percent": round(100 * done / total) if total else 0,
            "elapsed": human_duration(timezone.now() - item.started_at) if item.started_at else ""}


def _running(queryset):
    items = list(queryset)
    for item in items:
        item.job = job_progress(item)
        item.elapsed = item.job["elapsed"]
    return items


def eligible_guides():
    """Guides concernés : publiés, en français, avec un PDF."""
    with_pdf = GuideDocument.objects.exclude(file="").values("guide")
    return Guide.objects.filter(is_published=True, language=Language.FR, pk__in=with_pdf)


def _service(kind):
    item = control(kind)
    alive = bool(item.last_seen and timezone.now() - item.last_seen < ALIVE_AFTER)
    return {"control": item, "alive": alive, "gaming": gaming()}


def _chart(dates):
    """Barres des {CHART_DAYS} derniers jours, en attributs SVG (la CSP interdit les styles en
    ligne)."""
    today = timezone.localdate()
    counts = {
        row["day"]: row["n"]
        for row in dates.annotate(day=TruncDate("when")).values("day").annotate(n=Count("pk"))
    }
    days = [today - timedelta(days=offset) for offset in range(CHART_DAYS - 1, -1, -1)]
    peak = max([counts.get(day, 0) for day in days] + [1])
    bars = []
    for index, day in enumerate(days):
        value = counts.get(day, 0)
        height = round(38 * value / peak) if value else 0  # graphique plat : 280 × 60, axe à y = 48
        bars.append(
            {
                "day": day,
                "value": value,
                "x": index * 20 + 2,
                "y": 48 - height,
                "height": height,
                "label": day.strftime("%d/%m"),
            }
        )
    return {"bars": bars, "total": sum(b["value"] for b in bars), "peak": peak}


def _seconds(durations):
    return [d.total_seconds() for d in durations if d and d.total_seconds() > 0]


def _pace(durations, waiting, running=(), workers=1, average=None, waiting_seconds=None):
    """
    Temps de génération, au même format pour tous les types (chaîne vide quand on ne sait pas) :
      - last, average : durée du dernier document généré et moyenne des RECENT_FOR_PACE derniers
        (`durations` : timedelta, du plus récent au plus ancien) ;
      - elapsed : temps écoulé de la tâche en cours (la plus ancienne s'il y en a plusieurs) ;
      - remaining : reste estimé pour les `waiting` éléments en file et les tâches en cours.
    `average` (secondes) et `waiting_seconds` remplacent la moyenne et le coût de la file quand un
    type n'a pas un coût uniforme (podcasts : écriture puis enregistrement).
    """
    seconds = _seconds(durations)
    if average is None and seconds:
        average = sum(seconds) / len(seconds)
    now = timezone.now()
    elapsed = [(now - item.started_at).total_seconds() for item in running if item.started_at]
    remaining = ""
    if average and (waiting or running):
        if waiting_seconds is None:
            waiting_seconds = average * waiting
        left = max([average - e for e in elapsed], default=0) + waiting_seconds / max(workers, 1)
        remaining = human_duration(timedelta(seconds=left)) if left >= 60 else "moins d'une minute"
    return {"last": human_duration(timedelta(seconds=seconds[0])) if seconds else "",
            "average": human_duration(timedelta(seconds=average)) if average else "",
            "samples": len(seconds),
            "elapsed": human_duration(timedelta(seconds=max(elapsed))) if elapsed else "",
            "remaining": remaining}


def retryable_quiz_failures():
    """Échecs dont le guide n'a toujours ni QCM ni nouvelle génération en cours."""
    active = (
        QuizDraft.objects.filter(status__in=QuizDraft.ACTIVE).exclude(guide=None).values("guide")
    )  # NOT IN sans NULL
    return (
        QuizDraft.objects.filter(status=QuizStatus.FAILED, guide__in=eligible_guides())
        .exclude(guide__in=active)
        .exclude(guide__quiz_questions__isnull=False)
    )


def quiz_stats():
    service = _service(Kind.QUIZ)
    eligible = eligible_guides()
    drafts = QuizDraft.objects.select_related("guide", "referential")
    counts = dict(drafts.values_list("status").annotate(n=Count("pk")))
    pending = drafts.filter(status=QuizStatus.PENDING).order_by(*QuizDraft.QUEUE_ORDER)
    done = drafts.filter(
        status__in=[QuizStatus.REVIEW, QuizStatus.PUBLISHED],
        started_at__isnull=False,
        generated_at__isnull=False,
    ).order_by("-generated_at")[:RECENT_FOR_PACE]
    active_guides = drafts.filter(status__in=QuizDraft.ACTIVE).exclude(guide=None).values("guide")
    decisions = dict(QuizDraftQuestion.objects.values_list("decision").annotate(n=Count("pk")))
    running = _running(drafts.filter(status=QuizStatus.GENERATING).order_by("started_at"))
    return {
        **service,
        "eligible": eligible.count(),
        "covered": eligible.filter(quiz_questions__isnull=False).distinct().count(),
        "counts": {status: counts.get(status, 0) for status in QuizStatus.values},
        "running": running,
        "next": pending[:5],
        "pending": counts.get(QuizStatus.PENDING, 0),
        "failed": retryable_quiz_failures().select_related("guide").order_by("-requested_at")[:8],
        "failed_count": retryable_quiz_failures().values("guide").distinct().count(),
        "missing": (
            eligible.filter(quiz_questions__isnull=True).exclude(pk__in=active_guides).count()
        ),
        "questions": {
            "total": sum(decisions.values()),
            "accepted": decisions.get(QuizDraftQuestion.Decision.ACCEPTED, 0),
            "rejected": decisions.get(QuizDraftQuestion.Decision.REJECTED, 0),
        },
        "pace": _pace(
            [d.generated_at - d.started_at for d in done],
            counts.get(QuizStatus.PENDING, 0),
            running,
            service["control"].workers,
        ),
        "chart": _chart(
            QuizDraft.objects.filter(generated_at__isnull=False)
            .annotate(when=F("generated_at"))
            .filter(when__date__gte=timezone.localdate() - timedelta(days=CHART_DAYS - 1))
        ),
    }


def glossary_stats():
    from .models import Glossary  # évite une importation circulaire (glossary importe generation)
    from . import glossary
    service = _service(Kind.GLOSSARY)
    eligible = eligible_guides()
    items = Glossary.objects.select_related("guide")
    status = Glossary.Status
    counts = dict(items.values_list("status").annotate(n=Count("pk")))
    done = items.filter(status__in=[status.REVIEW, status.PUBLISHED], started_at__isnull=False,
                        generated_at__isnull=False).order_by("-generated_at")[:RECENT_FOR_PACE]
    failed = items.filter(status=status.FAILED)
    running = _running(items.filter(status=status.GENERATING).order_by("started_at"))
    return {
        **service,
        "eligible": eligible.count(),
        "covered": eligible.filter(glossary__status=status.PUBLISHED).count(),
        "counts": {value: counts.get(value, 0) for value in status.values},
        "running": running,
        "next": items.filter(status=status.PENDING).order_by(*Glossary.QUEUE_ORDER)[:5],
        "pending": counts.get(status.PENDING, 0),
        "failed": failed.order_by("-requested_at")[:8],
        "failed_count": failed.count(),
        "missing": glossary.guides_without_glossary().count(),
        "pace": _pace(
            [g.generated_at - g.started_at for g in done], counts.get(status.PENDING, 0), running
        ),
        "chart": _chart(
            Glossary.objects.filter(generated_at__isnull=False)
            .annotate(when=F("generated_at"))
            .filter(when__date__gte=timezone.localdate() - timedelta(days=CHART_DAYS - 1))
        ),
    }


BACKGROUND = (Kind.QUIZ, Kind.COURSE, Kind.PODCAST, Kind.GLOSSARY, Kind.AUDIO)


# --- Ordre de passage : une seule génération à la fois -------------------------------------------
# Toutes partagent la carte graphique (IA locale, voix) : en parallèle, chacune ralentit les autres
# et
# l'IA et la voix se disputent la mémoire. Une génération ne prend donc un nouvel élément que si
# aucune
# autre n'est en train de travailler et qu'aucune génération placée avant elle n'a du travail en
# attente.
# Les versions audio demandées par un utilisateur passent devant tout (réglable : users_first).
# L'élément en cours n'est jamais coupé.
DEFAULT_ORDER = (Kind.GLOSSARY, Kind.QUIZ, Kind.COURSE, Kind.PODCAST, Kind.AUDIO)


def order():
    """Générations dans leur ordre de passage (choisi sur le tableau de bord, sinon
    DEFAULT_ORDER)."""
    ranks = dict(
        GenerationControl.objects.filter(kind__in=BACKGROUND, rank__isnull=False).values_list(
            "kind", "rank"
        )
    )
    return sorted(BACKGROUND, key=lambda kind: (ranks.get(kind, 100 + DEFAULT_ORDER.index(kind)),
                                                DEFAULT_ORDER.index(kind)))


def move_in_order(kind, step):
    """Avance (step = -1) ou recule (+1) une génération dans l'ordre de passage."""
    kinds = order()
    index = kinds.index(kind)
    target = min(max(index + step, 0), len(kinds) - 1)
    kinds.insert(target, kinds.pop(index))
    for rank, item in enumerate(kinds, start=1):
        GenerationControl.objects.update_or_create(kind=item, defaults={"rank": rank})
    return kinds


def workload(kind):
    """(éléments en attente, éléments en cours, une demande d'utilisateur attend-elle ?) pour une
    génération."""
    from courses.models import Course  # imports locaux : ces applications importent catalogue
    from podcasts.models import Series
    from .models import Glossary
    if kind == Kind.QUIZ:
        items, waiting, working = QuizDraft.objects, [QuizStatus.PENDING], [QuizStatus.GENERATING]
    elif kind == Kind.GLOSSARY:
        items, waiting, working = (
            Glossary.objects,
            [Glossary.Status.PENDING],
            [Glossary.Status.GENERATING],
        )
    elif kind == Kind.COURSE:
        items, waiting, working = (
            Course.objects,
            [Course.Status.PENDING],
            [Course.Status.GENERATING],
        )
    elif kind == Kind.PODCAST:
        items = Series.objects
        waiting, working = [Series.Status.PENDING, Series.Status.TO_RECORD], [
            Series.Status.GENERATING,
            Series.Status.RECORDING,
        ]
    else:
        pending = Narration.objects.filter(status=Narration.Status.PENDING)
        return (
            pending.count(),
            Narration.objects.filter(status=Narration.Status.PROCESSING).count(),
            pending.filter(priority=0).exists(),
        )
    return items.filter(status__in=waiting).count(), items.filter(status__in=working).count(), False


def _available(kind):
    """Génération qui peut avancer : ni en pause, ni service arrêté (sinon elle ne bloque
    personne)."""
    item = control(kind)
    return not item.paused and bool(
        item.last_seen and timezone.now() - item.last_seen < ALIVE_AFTER
    )


def holder(kind):
    """
    Génération qui a la main et devant laquelle `kind` doit attendre, ou None si `kind` peut prendre
    un nouvel élément. Un service arrêté ou en pause ne bloque pas les autres.
    """
    if gaming():
        return None  # tout est en pause de toute façon
    loads = {k: workload(k) for k in BACKGROUND}
    available = {k: _available(k) for k in BACKGROUND}
    for other in BACKGROUND:  # quelqu'un travaille déjà : il termine son élément
        if other != kind and available[other] and loads[other][1]:
            return other
    sequence = order()
    if (
        loads[Kind.AUDIO][2] and available[Kind.AUDIO] and audio_users_first()
    ):  # un utilisateur attend son audio
        sequence = [Kind.AUDIO] + [k for k in sequence if k != Kind.AUDIO]
    for other in sequence:
        if other == kind:
            return None
        if available[other] and loads[other][0]:
            return other
    return None


def audio_users_first():
    """Les versions audio demandées depuis la page d'écoute passent-elles devant l'ordre de passage
    ?"""
    return control(Kind.AUDIO).users_first


def set_audio_users_first(on):
    GenerationControl.objects.update_or_create(kind=Kind.AUDIO, defaults={"users_first": on})


def my_turn(kind):
    return holder(kind) is None


def set_all_paused(paused, user=None):
    """Met en pause (ou relance) toutes les générations en arrière-plan d'un coup."""
    for kind in BACKGROUND:
        set_paused(kind, paused, user)


def paused_count():
    return GenerationControl.objects.filter(kind__in=BACKGROUND, paused=True).count()


# --- Lancement / arrêt des services (exécuté par l'agent du poste)
# ---------------------------------
AGENT_ALIVE_AFTER = timedelta(seconds=45)    # l'agent interroge toutes les 8 s
COMMAND_STALE_AFTER = timedelta(minutes=15)  # téléchargement du modèle compris


def agent_alive():
    item = GenerationControl.objects.filter(kind=Kind.AGENT).first()
    return bool(item and item.last_seen and timezone.now() - item.last_seen < AGENT_ALIVE_AFTER)


def current_command():
    """Demande en attente ou en cours, s'il y en a une."""
    return ServiceCommand.objects.filter(
        status__in=[ServiceCommand.Status.PENDING, ServiceCommand.Status.RUNNING]
    ).first()


def request_services(action, user=None):
    """Enregistre une demande pour l'agent ; une seule à la fois."""
    return current_command() or ServiceCommand.objects.create(action=action, requested_by=user)


def take_command():
    """L'agent signale qu'il tourne et prend la plus ancienne demande en attente."""
    heartbeat(Kind.AGENT)
    ServiceCommand.objects.filter(
        status=ServiceCommand.Status.RUNNING, started_at__lt=timezone.now() - COMMAND_STALE_AFTER
    ).update(
        status=ServiceCommand.Status.FAILED,
        message="Sans réponse de l'agent.",
        finished_at=timezone.now(),
    )
    with transaction.atomic():
        command = (ServiceCommand.objects.select_for_update(skip_locked=True)
                   .filter(status=ServiceCommand.Status.PENDING).order_by("requested_at").first())
        if command:
            command.status, command.started_at = ServiceCommand.Status.RUNNING, timezone.now()
            command.save(update_fields=["status", "started_at"])
        return command


def finish_command(pk, ok, message=""):
    ServiceCommand.objects.filter(pk=pk, status=ServiceCommand.Status.RUNNING).update(
        status=ServiceCommand.Status.DONE if ok else ServiceCommand.Status.FAILED,
        message=message[:2000], finished_at=timezone.now())


def audio_stats():
    from . import narration, narration_text  # import local : narration importe ce module
    service = _service(Kind.AUDIO)
    eligible = eligible_guides()
    narrations = Narration.objects.select_related("guide")
    counts = dict(narrations.values_list("status").annotate(n=Count("pk")))
    ready = narrations.filter(status=AudioStatus.READY)
    done = ready.filter(started_at__isnull=False, finished_at__isnull=False).order_by(
        "-finished_at"
    )[:RECENT_FOR_PACE]
    seconds = ready.aggregate(total=Sum("duration_seconds"))["total"] or 0
    running = _running(narrations.filter(status=AudioStatus.PROCESSING).order_by("started_at"))
    return {
        **service,
        "eligible": eligible.count(),
        "covered": (
            eligible.filter(
                narration__status=AudioStatus.READY, narration__version=narration_text.VERSION
            ).count()
        ),  # à jour seulement
        "outdated": narration.outdated_guides(eligible).count(),
        "counts": {status: counts.get(status, 0) for status in AudioStatus.values},
        "running": running,
        "next": narrations.filter(status=AudioStatus.PENDING).order_by(*Narration.QUEUE_ORDER)[:5],
        "pending": counts.get(AudioStatus.PENDING, 0),
        "failed": narrations.filter(status=AudioStatus.FAILED).order_by("-finished_at")[:8],
        "failed_count": counts.get(AudioStatus.FAILED, 0),
        "missing": eligible.filter(narration__isnull=True).count(),
        "hours": human_duration(timedelta(seconds=seconds)),
        "size_mb": round(seconds * 48 / 8 / 1024),  # MP3 à 48 kbit/s
        "pace": _pace(
            [n.finished_at - n.started_at for n in done],
            counts.get(AudioStatus.PENDING, 0),
            running,
        ),
        "chart": _chart(
            Narration.objects.filter(status=AudioStatus.READY, finished_at__isnull=False)
            .annotate(when=F("finished_at"))
            .filter(when__date__gte=timezone.localdate() - timedelta(days=CHART_DAYS - 1))
        ),
    }
