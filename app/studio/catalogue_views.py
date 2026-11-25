"""
Éditeur commun des contenus du catalogue : guide, domaine, référentiel, programme d'examen,
certification, parcours sur mesure. Chaque type a son formulaire principal et, au besoin, des blocs
complémentaires (documents d'un guide, grille d'exigences, documents cités, étapes d'un parcours).
"""

from dataclasses import dataclass, field

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from catalogue.importers.citations import best_guide, parse_citations
from catalogue.importers.grids import rows_from_outline, sync_requirements
from catalogue.importers.guides import reading_minutes
from catalogue.importers.pdf import outline, page_count
from catalogue.importers.textgrid import pdf_text
from catalogue.importers.trames import parse_trame
from catalogue.models import (
    Certification,
    Guide,
    GuideCategory,
    GuideCitation,
    GuideDocument,
    ReadingPath,
    ReadingPathStep,
    Referential,
    Requirement,
)

from . import ai, editorial, grid, sources
from .forms import (
    CategoryForm,
    CertificationForm,
    DocumentForm,
    ExamProgramForm,
    GuideForm,
    PathForm,
    ReferentialForm,
)
from .permissions import author_required
from .registry import BY_KEY


@dataclass
class Spec:
    model: type
    form: type
    queryset: object = None          # restriction (type de référentiel)
    defaults: dict = field(default_factory=dict)
    title_field: str = "title"
    blocks: tuple = ()               # blocs complémentaires affichés sous le formulaire
    editorial: bool = True           # cycle rédaction → relecture → publication


SPECS = {
    "guide": Spec(Guide, GuideForm, blocks=("documents",)),
    "domaine": Spec(
        GuideCategory, CategoryForm, title_field="name", editorial=False, blocks=("members",)
    ),
    "referentiel": Spec(
        Referential,
        ReferentialForm,
        title_field="name",
        blocks=("grid", "citations"),
        queryset=lambda: Referential.objects.exclude(kind=Referential.Kind.EXAM),
        defaults={"kind": Referential.Kind.ANSSI},
    ),
    "programme": Spec(
        Referential,
        ExamProgramForm,
        title_field="name",
        blocks=("grid",),
        queryset=lambda: Referential.objects.filter(kind=Referential.Kind.EXAM),
        defaults={"kind": Referential.Kind.EXAM},
    ),
    "certification": Spec(Certification, CertificationForm, title_field="name"),
    "parcours": Spec(ReadingPath, PathForm, blocks=("steps",)),
}


def _spec(key, user):
    if key not in SPECS:
        raise Http404("Type inconnu.")
    if BY_KEY[key].manager_only and not user.is_content_manager:
        raise PermissionDenied("Réservé aux gestionnaires de contenu.")
    return SPECS[key]


def _object(key, spec, pk):
    queryset = spec.queryset() if spec.queryset else spec.model.objects.all()
    return get_object_or_404(queryset, pk=pk)


def _can_edit(user, spec, item):
    return user.is_content_manager if not spec.editorial else editorial.can_edit(user, item)


def _unique_slug(model, text):
    base = slugify(text)[:180] or "sans-titre"
    slug, n = base, 2
    while model.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def _edit_url(key, item, anchor=""):
    return reverse("studio:edit", args=[key, item.pk]) + anchor


def _public_url(item):
    try:
        return item.get_absolute_url() if getattr(item, "is_published", True) else ""
    except AttributeError:
        return ""


@author_required
def editor(request, key, pk=None):
    """Création (pk vide) ou modification d'un contenu du catalogue."""
    spec = _spec(key, request.user)
    item = _object(key, spec, pk) if pk else None
    if item is not None and spec.editorial and not editorial.can_view(request.user, item):
        raise PermissionDenied("Ce contenu appartient à un autre rédacteur.")
    editable = item is None or _can_edit(request.user, spec, item)
    form = spec.form(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST":
        if not editable:
            messages.error(request, "Ce contenu n'est pas modifiable en ce moment.")
            return redirect(_edit_url(key, item))
        if form.is_valid():
            creating = item is None
            item = form.save(commit=False)
            for name, value in spec.defaults.items():
                if creating:
                    setattr(item, name, value)
            if creating:
                item.slug = _unique_slug(spec.model, getattr(item, spec.title_field))
                if spec.editorial:
                    editorial.start(item, request.user)
            if isinstance(item, Referential) and "document" in form.changed_data and item.document:
                data = item.document.read()
                item.page_count = page_count(data)
                item.document.seek(0)
            item.save()
            form.save_m2m()
            messages.success(
                request,
                f"« {getattr(item, spec.title_field)} » {'créé' if creating else 'enregistré'}."
                + (
                    " Complétez-le, puis soumettez-le à la relecture."
                    if creating and spec.editorial and not request.user.is_content_manager
                    else ""
                ),
            )
            return redirect(_edit_url(key, item))
        messages.error(request, "Le formulaire contient des erreurs : voir les champs signalés.")
    return render(request, "studio/editor.html", _context(request, key, spec, item, form, editable))


def _context(request, key, spec, item, form, editable):
    user = request.user
    context = {
        "type": BY_KEY[key],
        "key": key,
        "item": item,
        "form": form,
        "editable": editable,
        "spec": spec,
        "manager": user.is_content_manager,
        "public_url": _public_url(item) if item else "",
        "back_url": (
            sources.list_url(
                key, getattr(item, "editorial_status", "redaction") if item else "redaction"
            )
            if key != "domaine"
            else sources.list_url(key, "")
        ),
        "status": (
            item.get_editorial_status_display() if item is not None and spec.editorial else ""
        ),
        "draft": item is not None and spec.editorial and editorial.is_draft(item),
        "deletable": item is not None and spec.editorial and editorial.deletable(user, item),
        "ai_busy": ai.available(),
        "ai_target": "summary" if key == "guide" else "description",
    }
    context["new_hint"] = (
        "Remplissez le formulaire puis « Créer » : rien n'est visible des apprenants avant la "
        "publication."
        if spec.editorial
        else "Remplissez le formulaire puis « Créer »."
    )
    if item is None:
        return context
    if spec.editorial:
        problems = _problems(key, item) if editorial.is_draft(item) else []
        context["problems"] = problems
        context["hint"] = _hint(user, item, problems)
    source = {"guide": f"guide:{item.pk}", "referentiel": f"referentiel:{item.pk}",
              "programme": f"referentiel:{item.pk}"}.get(key, "")
    context["ai_source"] = source
    context["ai_category"] = getattr(getattr(item, "category", None), "slug", "")
    context["ai_actions"] = ([("resumer", "Résumer le document")] if source else []) + [
        ("simplifier", "Simplifier"),
        ("corriger", "Corriger"),
    ]
    if "documents" in spec.blocks:
        context.update(documents=list(item.documents.all()), document_form=DocumentForm())
    if "grid" in spec.blocks:
        context.update(grid_text=grid.to_text(item), grid_count=item.requirements.count(),
                       grid_help=grid.__doc__, exam=item.kind == Referential.Kind.EXAM)
    if "citations" in spec.blocks:
        context.update(
            citations=list(item.citations.select_related("guide")),
            guides=Guide.objects.filter(is_published=True).only("pk", "title").order_by("title"),
        )
    if "members" in spec.blocks:
        from courses.models import Course
        from podcasts.models import Series
        context.update(
            members=list(item.guides.order_by("level", "title")),
            other_guides=Guide.objects.exclude(category=item)
            .select_related("category")
            .order_by("title"),
            other_domains=GuideCategory.objects.exclude(pk=item.pk),
            certifications=(
                list(item.certifications.order_by("name"))
                if hasattr(item, "certifications")
                else []
            ),
            other_certifications=Certification.objects.exclude(guide_categories=item).order_by(
                "name"
            ),
            courses=list(Course.objects.filter(category=item).order_by("title")),
            series=list(Series.objects.filter(category=item).order_by("title")),
            custom_paths=list(item.custom_paths.order_by("title")),
        )
    if "steps" in spec.blocks:
        steps = list(item.steps.select_related("guide", "guide__category"))
        context.update(steps=steps, guides=Guide.objects.filter(is_published=True).exclude(
            pk__in=[s.guide_id for s in steps]).only("pk", "title").order_by("title"))
    return context


@author_required
@require_POST
def action(request, key, pk):
    """Soumettre, publier, renvoyer ou supprimer un contenu du catalogue."""
    spec = _spec(key, request.user)
    item = _object(key, spec, pk)
    user, verb = request.user, request.POST.get("action")
    title = getattr(item, spec.title_field)
    if not spec.editorial:
        if verb == "supprimer" and user.is_content_manager and isinstance(item, GuideCategory) \
                and not item.guides.exists():
            item.delete()
            messages.info(request, f"Domaine « {title} » supprimé.")
            return redirect("studio:list", key)
        messages.error(request, "Action impossible.")
        return redirect(_edit_url(key, item))
    if not editorial.can_view(user, item):
        raise PermissionDenied("Ce contenu appartient à un autre rédacteur.")
    if (
        verb == "soumettre"
        and editorial.can_edit(user, item)
        and item.editorial_status == editorial.WRITING
    ):
        problems = _problems(key, item)
        if problems:
            messages.error(request, "Avant de soumettre : " + " ; ".join(problems) + ".")
            return redirect(_edit_url(key, item))
        editorial.submit(item)
        messages.success(
            request, f"« {title} » est soumis à la relecture d'un gestionnaire de contenu."
        )
    elif verb == "publier" and user.is_content_manager and editorial.is_draft(item):
        problems = _problems(key, item)
        if problems:
            messages.error(request, "Avant de publier : " + " ; ".join(problems) + ".")
            return redirect(_edit_url(key, item))
        editorial.publish(item)
        messages.success(request, f"« {title} » est publié : il est visible des apprenants.")
    elif (
        verb == "renvoyer" and user.is_content_manager and item.editorial_status == editorial.REVIEW
    ):
        editorial.send_back(item)
        messages.info(request, f"« {title} » est renvoyé à son rédacteur.")
    elif verb == "supprimer" and editorial.deletable(user, item):
        if isinstance(item, Guide):
            for document in item.documents.all():
                document.file.delete(save=False)
        item.delete()
        messages.info(request, f"« {title} » supprimé.")
        return redirect("studio:list", key)
    else:
        messages.error(request, "Action impossible en ce moment.")
    return redirect(_edit_url(key, item))


def _hint(user, item, problems):
    """Ce qu'il faut faire ensuite, selon l'état et le rôle (affiché dans la barre d'état)."""
    todo = f" À faire avant : {' ; '.join(problems)}." if problems else ""
    if item.editorial_status == editorial.WRITING:
        if user.is_content_manager:
            return "Brouillon : publiez quand c'est prêt." + todo
        return "Brouillon : soumettez quand c'est prêt, un gestionnaire relira et publiera." + todo
    if item.editorial_status == editorial.REVIEW:
        if user.is_content_manager:
            return "Soumis : publiez, ou renvoyez au rédacteur avec vos remarques." + todo
        return "En relecture : plus modifiable, sauf s'il vous est renvoyé."
    return "Visible des apprenants."


def _problems(key, item):
    """Ce qu'il manque avant la relecture ou la publication."""
    problems = []
    if key == "guide":
        if not item.category_id:
            problems.append("choisir un domaine")
        if not item.documents.exclude(file="").exists():
            problems.append("ajouter le PDF du guide")
        if len(item.summary.strip()) < 40:
            problems.append("écrire un résumé")
    elif key in ("referentiel", "programme"):
        if not item.requirements.exists():
            problems.append(
                "écrire la grille" if key == "referentiel" else "écrire les domaines du programme"
            )
    elif key == "certification":
        if len(item.description.strip()) < 40:
            problems.append("écrire une description")
    elif key == "parcours":
        if item.steps.count() < 2:
            problems.append("choisir au moins deux guides")
    return problems


# --- Blocs complémentaires
# ---------------------------------------------------------------------------
def _editable_or_403(request, key, pk):
    spec = _spec(key, request.user)
    item = _object(key, spec, pk)
    if not _can_edit(request.user, spec, item):
        raise PermissionDenied("Ce contenu n'est pas modifiable en ce moment.")
    return item


@author_required
@require_POST
def guide_document(request, pk):
    """Ajouter un PDF à un guide (pages et durée de lecture calculées), ou en retirer un."""
    guide = _editable_or_403(request, "guide", pk)
    if request.POST.get("action") == "retirer":
        document = get_object_or_404(GuideDocument, pk=request.POST.get("document"), guide=guide)
        document.file.delete(save=False)
        document.delete()
        guide.text_sections.all().delete()  # texte à réextraire (recherche, assistant)
        messages.info(request, "Document retiré.")
        return redirect(_edit_url("guide", guide, "#documents"))
    form = DocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
        return redirect(_edit_url("guide", guide, "#documents"))
    upload = form.cleaned_data["file"]
    data = upload.read()
    upload.seek(0)
    pages = page_count(data)
    position = (guide.documents.aggregate(n=Max("position"))["n"] or 0) + 1
    GuideDocument.objects.create(
        guide=guide,
        label=form.cleaned_data["label"],
        file=upload,
        page_count=pages,
        position=position,
    )
    if pages and position == 1:  # premier PDF : pages et durée de lecture du guide
        guide.page_count, guide.estimated_minutes = pages, reading_minutes(pages)
        guide.save(update_fields=["page_count", "estimated_minutes"])
    guide.text_sections.all().delete()
    messages.success(
        request,
        f"PDF ajouté{f' ({pages} pages, ≈ {reading_minutes(pages)} min de lecture)' if pages else ''}.",
    )
    return redirect(_edit_url("guide", guide, "#documents"))


@author_required
@require_POST
def grid_save(request, key, pk):
    """Grille d'exigences (ou domaines d'un programme) : texte saisi, trame Excel ou sommaire du
    PDF."""
    referential = _editable_or_403(request, key, pk)
    source = request.POST.get("action", "texte")
    default_kind = Requirement.Kind.DOMAIN if key == "programme" else Requirement.Kind.REQUIREMENT
    if source == "trame":
        if not referential.trame:
            messages.error(request, "Ajoutez d'abord la trame Excel dans le formulaire ci-dessus.")
            return redirect(_edit_url(key, referential, "#grille"))
        with referential.trame.open("rb") as handle:
            rows = parse_trame(handle.read())
        origin = referential.trame_label or "Trame d'évaluation"
    elif source == "sommaire":
        if not referential.document:
            messages.error(request, "Ajoutez d'abord le PDF dans le formulaire ci-dessus.")
            return redirect(_edit_url(key, referential, "#grille"))
        with referential.document.open("rb") as handle:
            rows = rows_from_outline(outline(handle.read()))
        origin = "Sections du sommaire du PDF"
    else:
        rows = grid.parse(request.POST.get("grid", ""), default_kind)
        origin = "Grille saisie dans l'atelier"
    if not rows:
        messages.error(request, "Aucune ligne trouvée : la grille n'a pas été modifiée.")
        return redirect(_edit_url(key, referential, "#grille"))
    count = sync_requirements(referential, rows)
    referential.grid_source = origin
    referential.save(update_fields=["grid_source"])
    messages.success(
        request,
        f"Grille enregistrée : {count} ligne(s). La progression des apprenants est gardée pour "
        "les lignes dont la référence n'a pas changé.",
    )
    return redirect(_edit_url(key, referential, "#grille"))


@author_required
@require_POST
def citations_save(request, pk):
    """Documents cités par un référentiel : ajout, rattachement à un guide, retrait, extraction du
    PDF."""
    referential = _editable_or_403(request, "referentiel", pk)
    verb = request.POST.get("action")
    guides = list(Guide.objects.filter(is_published=True))
    if verb == "extraire":
        if not referential.document:
            messages.error(request, "Ajoutez d'abord le PDF du référentiel.")
            return redirect(_edit_url("referentiel", referential, "#citations"))
        with referential.document.open("rb") as handle:
            found = [c for c in parse_citations(pdf_text(handle.read())) if c.is_anssi]
        known = {c.title.lower() for c in referential.citations.all()}
        start = (referential.citations.aggregate(n=Max("position"))["n"] or 0) + 1
        added = [GuideCitation(referential=referential, code=c.code[:60], title=c.title[:500],
                               guide=best_guide(c.title, guides), position=start + n)
                 for n, c in enumerate(c for c in found if c.title.lower() not in known)]
        GuideCitation.objects.bulk_create(added)
        messages.success(
            request,
            f"{len(added)} document(s) cité(s) trouvé(s) dans le PDF ; vérifiez les guides"
            " associés.",
        )
    elif verb == "ajouter":
        title = request.POST.get("title", "").strip()
        guide = Guide.objects.filter(pk=request.POST.get("guide") or None).first()
        if not title and guide:
            title = guide.title
        if not title:
            messages.error(request, "Indiquez le titre du document cité (ou choisissez un guide).")
        else:
            position = (referential.citations.aggregate(n=Max("position"))["n"] or 0) + 1
            GuideCitation.objects.create(
                referential=referential,
                code=request.POST.get("code", "").strip()[:60],
                title=title[:500],
                guide=guide or best_guide(title, guides),
                position=position,
            )
            messages.success(request, "Document cité ajouté.")
    elif verb in ("relier", "retirer"):
        citation = get_object_or_404(
            GuideCitation, pk=request.POST.get("citation"), referential=referential
        )
        if verb == "retirer":
            citation.delete()
            messages.info(request, "Document cité retiré.")
        else:
            citation.guide = Guide.objects.filter(pk=request.POST.get("guide") or None).first()
            citation.save(update_fields=["guide"])
            messages.success(request, "Guide associé enregistré.")
    return redirect(_edit_url("referentiel", referential, "#citations"))


@author_required
@require_POST
def domain_members(request, pk):
    """Ce qui appartient à un domaine : ses guides (ajout, déplacement) et les certifications qu'il
    prépare."""
    domain = _editable_or_403(request, "domaine", pk)
    verb = request.POST.get("action")
    if verb in ("ajouter-guide", "deplacer-guide"):
        guide = get_object_or_404(Guide, pk=request.POST.get("guide"))
        target = (
            domain
            if verb == "ajouter-guide"
            else GuideCategory.objects.filter(pk=request.POST.get("vers") or None).first()
        )
        guide.category = target
        guide.save(update_fields=["category"])
        messages.success(
            request,
            f"« {guide.title} » est maintenant dans"
            f" {'« ' + target.name + ' »' if target else 'aucun domaine'}.",
        )
    elif verb in ("lier-certification", "delier-certification"):
        certification = get_object_or_404(Certification, pk=request.POST.get("certification"))
        if verb == "lier-certification":
            certification.guide_categories.add(domain)
            messages.success(request, f"« {certification.name} » est rattachée au domaine.")
        else:
            certification.guide_categories.remove(domain)
            messages.info(request, f"« {certification.name} » n'est plus rattachée au domaine.")
    return redirect(_edit_url("domaine", domain, "#contenu"))


@author_required
@require_POST
def path_step(request, pk):
    """Étapes d'un parcours : ajouter un guide, déplacer, annoter, retirer."""
    path = _editable_or_403(request, "parcours", pk)
    verb = request.POST.get("action")
    steps = list(path.steps.all())
    if verb == "ajouter":
        guide = get_object_or_404(Guide, pk=request.POST.get("guide"), is_published=True)
        if path.steps.filter(guide=guide).exists():
            messages.info(request, "Ce guide fait déjà partie du parcours.")
        else:
            ReadingPathStep.objects.create(path=path, guide=guide, position=len(steps) + 1,
                                           note=request.POST.get("note", "").strip()[:300])
            messages.success(request, f"« {guide.title} » ajouté au parcours.")
        return redirect(_edit_url("parcours", path, "#etapes"))
    step = get_object_or_404(ReadingPathStep, pk=request.POST.get("step"), path=path)
    if verb == "note":
        step.note = request.POST.get("note", "").strip()[:300]
        step.save(update_fields=["note"])
    elif verb == "retirer":
        step.delete()
        steps = [s for s in steps if s.pk != step.pk]
    elif verb in ("monter", "descendre"):
        index = next(i for i, s in enumerate(steps) if s.pk == step.pk)
        target = index - 1 if verb == "monter" else index + 1
        if 0 <= target < len(steps):
            steps.insert(target, steps.pop(index))
    with transaction.atomic():
        for n, item in enumerate(steps, start=1):
            if item.position != n:
                ReadingPathStep.objects.filter(pk=item.pk).update(position=n)
    return redirect(_edit_url("parcours", path, f"#s{step.pk}" if verb != "retirer" else "#etapes"))
