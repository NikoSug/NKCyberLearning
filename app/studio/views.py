"""
Atelier de rédaction : les rédacteurs écrivent eux-mêmes des cours et des podcasts, avec l'aide de
l'IA
locale si besoin, puis les soumettent à la relecture d'un gestionnaire de contenu.

L'écriture se fait dans les mêmes pages que la relecture (une leçon ou un épisode par page) : elles
s'ouvrent au rédacteur pour ses propres contenus « en rédaction ».
"""

import json

from django import forms
from django.contrib import messages
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.ratelimit import rate_limit
from catalogue import assistant, generation
from catalogue.models import AudioUpload, Glossary, Guide, GuideCategory, Level, QuizDraft
from courses import coursegen
from courses.models import Course, CourseQuestion, Lesson
from podcasts import podcastgen
from podcasts.models import Episode, Series

from . import ai, editorial, registry, sources
from .permissions import author_required, can_edit, check_view

class LearningForm(forms.Form):
    """Nouveau cours ou podcast : le type vient de la carte choisie, le départ du bouton (moi ou
    l'IA)."""
    title = forms.CharField(label="Titre", min_length=5, max_length=200)
    category = forms.ModelChoiceField(
        label="Domaine",
        queryset=GuideCategory.objects.all(),
        empty_label=None,
        help_text="Ses guides servent de sources à l'IA.",
    )
    level = forms.ChoiceField(label="Niveau", choices=Level.choices, initial=Level.BEGINNER)
    brief = forms.CharField(
        label="De quoi voulez-vous parler ?",
        required=False,
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=(
            "Le sujet, le public, les points à aborder ou à éviter, le ton… L'IA s'en sert "
            "pour le premier jet et pour chaque aide."
        ),
    )

    SECTIONS = [("Le contenu", ["title", "category", "level", "brief"])]

    def sections(self):
        return [(title, [self[name] for name in names]) for title, names in self.SECTIONS]


def _unique_slug(model, title, prefix):
    base = f"{prefix}-{slugify(title)[:80] or 'sans-titre'}"
    slug, n = base, 2
    while model.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def editor_url(item):
    if isinstance(item, Course):
        return reverse("courses:review_course", args=[item.pk])
    return reverse("podcasts:review_series", args=[item.pk])


@author_required
def home(request):
    """Accueil de l'atelier : une carte par type de contenu, mes brouillons, et ce qui attend une
    relecture."""
    user = request.user
    types = registry.visible_types(user)
    cards = []
    for content in types:
        counts = sources.counts(content.key, user)
        cards.append({"type": content, **counts})
    groups = [
        {
            "key": key,
            "label": label,
            "intro": intro,
            "cards": [c for c in cards if c["type"].group == key],
        }
        for key, label, intro in registry.GROUPS
    ]
    drafts = [
        (content, entry)
        for content in types
        for entry in sources.entries(content.key, user, "brouillons")
    ]
    drafts.sort(key=lambda pair: pair[1].when or timezone.now(), reverse=True)
    to_review = []
    if user.is_content_manager:
        to_review = [
            (content, entry)
            for content in types
            for entry in sources.entries(content.key, user, "relecture")
        ]
    return render(request, "studio/hub.html", {
        "groups": groups, "drafts": drafts[:12], "draft_count": len(drafts), "to_review": to_review,
    })


@author_required
def type_list(request, key):
    """Tous les contenus d'un type, par état (brouillons, à relire, publiés), avec recherche."""
    content = registry.BY_KEY.get(key)
    if content is None or content not in registry.visible_types(request.user):
        raise Http404("Type inconnu.")
    bucket = request.GET.get("etat", "brouillons" if key != "domaine" else "publies")
    bucket = bucket if bucket in dict(sources.BUCKETS) else "brouillons"
    query = request.GET.get("q", "").strip()[:80]
    counts = {b: len(sources.entries(key, request.user, b)) for b, _label in sources.BUCKETS}
    return render(
        request,
        "studio/list.html",
        {
            "type": content,
            "bucket": bucket,
            "buckets": sources.BUCKETS,
            "counts": counts,
            "query": query,
            "entries": sources.entries(key, request.user, bucket, query),
            "status_labels": sources.STATUS_LABELS,
        },
    )


class GuideChoiceForm(forms.Form):
    guide = forms.ModelChoiceField(
        label="Guide", queryset=Guide.objects.none(), empty_label="— Choisir un guide —"
    )

    def __init__(self, *args, queryset=None, key="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["guide"].queryset = queryset
        if key == "lexique":
            self.fields["guide"].label_from_instance = _with_glossary


def _with_glossary(guide):
    """« Titre — lexique publié (42 entrées) » quand le guide en a déjà un : il sera complété."""
    item = getattr(guide, "glossary", None) if hasattr(guide, "glossary") else None
    if item is None or item.status in ("echec", "abandonne"):
        return guide.title
    return (
        f"{guide.title} — lexique {item.get_status_display().lower()} ({item.entries.count()}"
        " entrées)"
    )


def _guides_for(key):
    """Guides proposés : publiés, avec un PDF (pour un lexique, avec celui qu'ils ont peut-être
    déjà)."""
    guides = generation.eligible_guides().order_by("title")
    if key == "lexique":
        guides = guides.select_related("glossary")
    return guides


@author_required
def create(request, key):
    """Ajouter un contenu : formulaire du catalogue, cours ou podcast, ou choix du guide (QCM,
    lexique, audio)."""
    content = registry.BY_KEY.get(key)
    if content is None or content not in registry.visible_types(request.user):
        raise Http404("Type inconnu.")
    if key in ("cours", "podcast"):
        return new(request, key)
    if key not in ("qcm", "lexique", "audio"):
        return redirect("studio:edit_new", key)
    form = GuideChoiceForm(request.POST or None, queryset=_guides_for(key), key=key)
    if request.method == "POST" and form.is_valid():
        guide, user = form.cleaned_data["guide"], request.user
        if key == "qcm":
            draft = QuizDraft.objects.create(
                guide=guide, author=user, requested_by=user, status="redaction"
            )
            return redirect("studio:qcm_edit", draft.pk)
        if key == "lexique":
            item = Glossary.objects.filter(guide=guide).first()
            if item is not None and item.status not in ("echec", "abandonne"):
                # Un lexique par guide : on ouvre celui qui existe, pour le compléter.
                if user.is_content_manager or item.author_id == user.pk:
                    return redirect("catalogue:glossary_review", item.pk)
                form.add_error(
                    "guide",
                    f"Ce guide a déjà un lexique ({item.get_status_display().lower()}) : un "
                    "gestionnaire de contenu peut le compléter.",
                )
                return render(request, "studio/pick_guide.html", {"type": content, "form": form})
            if item is None:
                item = Glossary.objects.create(
                    guide=guide, author=user, requested_by=user, status="redaction"
                )
            else:  # ancien lexique abandonné ou en échec : repris par le rédacteur
                item.entries.all().delete()
                item.author, item.requested_by, item.status, item.error = (
                    user,
                    user,
                    "redaction",
                    "",
                )
                item.save()
            return redirect("catalogue:glossary_review", item.pk)
        upload = AudioUpload(guide=guide)
        editorial.start(upload, user)
        upload.save()
        return redirect("studio:audio_edit", upload.pk)
    return render(request, "studio/pick_guide.html", {"type": content, "form": form})


@author_required
def new(request, key):
    """Nouveau cours ou podcast ; deux boutons : « Écrire moi-même » ou « Premier jet par l'IA »."""
    content = registry.BY_KEY[key]
    form = LearningForm(request.POST or None)
    by_ai = request.POST.get("mode") == "ia"
    if request.method == "POST" and form.is_valid():
        data, user = form.cleaned_data, request.user
        if by_ai and len(data["brief"].strip()) < 20:
            form.add_error(
                "brief",
                "Pour un premier jet par l'IA, décrivez en quelques phrases ce dont il faut"
                " parler.",
            )
        else:
            common = {
                "category": data["category"],
                "title": data["title"].strip(),
                "level": data["level"],
                "brief": data["brief"].strip(),
                "author": user,
                "requested_by": user,
                "status": "attente" if by_ai else "redaction",
                "requested_at": timezone.now(),
            }
            if key == "cours":
                item = Course.objects.create(
                    kind=Course.Kind.THEME,
                    slug=_unique_slug(Course, data["title"], "atelier"),
                    **common,
                )
            else:
                item = Series.objects.create(
                    slug=_unique_slug(Series, data["title"], "atelier"), **common
                )
            if by_ai:
                messages.success(
                    request,
                    f"« {item.title} » : l'IA écrira le premier jet à son tour (générations en "
                    "arrière-plan) ; il reviendra ensuite dans vos brouillons.",
                )
                return redirect("studio:list", key)
            unit = "leçon" if key == "cours" else "épisode"
            messages.success(
                request,
                f"« {item.title} » est créé : ajoutez sa première {unit} avec « +"
                f" {unit.capitalize()} ».",
            )
            return redirect(editor_url(item))
    return render(request, "studio/new_learning.html", {"form": form, "type": content})


# --- Aide de l'IA ------------------------------------------------------------------------------
@author_required
@require_POST
@rate_limit("ia-atelier", 60, 3600)
def ai_stream(request):
    """Brouillon, amélioration ou vérification d'un passage : réponse de l'IA au fil de l'eau."""
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Requête invalide."}, status=400)
    fields = {key: str(payload.get(key, ""))[:ai.MAX_TEXT]
              for key in ("text", "title", "objective", "brief", "category", "source")}
    try:
        answer = ai.stream(str(payload.get("action", "")), str(payload.get("kind", "")), **fields)
    except assistant.AssistantError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    response = StreamingHttpResponse(answer, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response


def _busy(request, back):
    reason = ai.available()
    if reason:
        messages.error(request, reason)
        return redirect(back)
    return None


@author_required
@require_POST
@rate_limit("ia-atelier", 60, 3600)
def ai_questions(request, pk):
    """Questions d'une leçon (ou examen final si `pk` est un cours et `examen` est demandé),
    rédigées par l'IA."""
    course = get_object_or_404(Course, pk=pk)
    check_view(request.user, course)
    exam = request.POST.get("lecon") == "examen"
    step = "examen" if exam else request.POST.get("lecon", "1")
    back = reverse("courses:review_course", args=[course.pk]) + f"?etape={step}"
    if not can_edit(request.user, course):
        messages.error(request, "Ce cours n'est plus modifiable.")
        return redirect(back)
    blocked = _busy(request, back)
    if blocked:
        return blocked
    try:
        if exam:
            if not course.lessons.exists():
                messages.error(
                    request, "Écrivez d'abord les leçons : l'examen porte sur leur contenu."
                )
                return redirect(back)
            coursegen.write_exam(course)
            messages.success(request, "Examen final rédigé par l'IA : relisez chaque question.")
        else:
            lesson = get_object_or_404(Lesson, course=course, position=int(step))
            if len(lesson.body) < 200:
                messages.error(request, "La leçon est trop courte pour en tirer des questions.")
                return redirect(back)
            existing = list(lesson.questions.values_list("text", flat=True))
            found = coursegen.more_questions(
                lesson.body, coursegen.LESSON_QUESTIONS, existing, "de la leçon"
            )
            start = lesson.questions.count()
            for n, q in enumerate(found, start=start + 1):
                CourseQuestion.objects.create(
                    course=course,
                    lesson=lesson,
                    position=n,
                    text=q["text"],
                    choices=q["choices"],
                    explanation=q["explanation"],
                )
            messages.success(
                request, f"{len(found)} question(s) ajoutée(s) par l'IA : relisez-les."
            )
    except (coursegen.GenerationError, ValueError) as exc:
        messages.error(request, f"L'IA n'a pas pu rédiger les questions : {exc}")
    return redirect(back)


@author_required
@require_POST
@rate_limit("ia-atelier", 60, 3600)
def ai_check_episode(request, pk):
    """Vérification de la fidélité d'un épisode aux guides : les répliques douteuses sont
    signalées."""
    episode = get_object_or_404(Episode.objects.select_related("series__category"), pk=pk)
    series = episode.series
    check_view(request.user, series)
    back = reverse("podcasts:review_series", args=[series.pk]) + f"?episode={episode.position}"
    blocked = _busy(request, back)
    if blocked:
        return blocked
    context = ai.context_for(
        series.category.slug, f"{episode.title} {episode.summary} {series.brief}"
    )
    episode.flags = podcastgen.check_script(episode.script, context)
    episode.save(update_fields=["flags"])
    count = len(episode.flags)
    messages.success(
        request,
        (
            f"Vérification terminée : {count} réplique{'s' if count > 1 else ''} à vérifier."
            if count
            else "Vérification terminée : rien à signaler."
        ),
    )
    return redirect(back)
