"""Atelier : version audio d'un guide enregistrée par un rédacteur (un MP3 par chapitre)."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from catalogue.models import AudioUpload, AudioUploadPart

from . import audio, editorial, sources
from .permissions import author_required


class PartForm(forms.Form):
    title = forms.CharField(label="Titre du chapitre", max_length=255)
    audio = forms.FileField(label="Fichier MP3")

    def clean_audio(self):
        upload = self.cleaned_data["audio"]
        if upload.size > audio.MAX_BYTES:
            raise forms.ValidationError("Fichier trop gros (55 Mo au plus par chapitre).")
        data = upload.read()
        upload.seek(0)
        duration = audio.mp3_duration(data)
        if not duration:
            raise forms.ValidationError("Ce fichier n'est pas un MP3 lisible.")
        self.duration = duration
        return upload


def _upload(request, pk):
    upload = get_object_or_404(AudioUpload.objects.select_related("guide", "author"), pk=pk)
    if not (request.user.is_content_manager or upload.author_id == request.user.pk):
        raise PermissionDenied("Cet enregistrement appartient à un autre rédacteur.")
    return upload


def _url(upload, anchor=""):
    return reverse("studio:audio_edit", args=[upload.pk]) + anchor


@author_required
def audio_edit(request, pk):
    upload = _upload(request, pk)
    parts = list(upload.parts.all())
    return render(
        request,
        "studio/audio_edit.html",
        {
            "upload": upload,
            "parts": parts,
            "form": PartForm(initial={"title": f"Chapitre {len(parts) + 1}"}),
            "editable": editorial.can_edit(request.user, upload),
            "manager": request.user.is_content_manager,
            "problems": audio.problems(upload),
            "total": round(sum(p.duration_seconds for p in parts) / 60),
            "current": getattr(upload.guide, "narration", None),
            "hint": _hint(request.user, upload),
            "back_url": sources.list_url("audio", upload.editorial_status),
        },
    )


def _hint(user, upload):
    problems = audio.problems(upload) if upload.editorial_status else []
    todo = f" À faire avant : {' ; '.join(problems)}." if problems else ""
    if upload.editorial_status == editorial.WRITING:
        return (
            "Brouillon : publiez quand c'est prêt (la voix de synthèse du guide sera remplacée)."
            if user.is_content_manager
            else "Brouillon : soumettez quand c'est prêt, un gestionnaire écoutera puis publiera."
        ) + todo
    if upload.editorial_status == editorial.REVIEW:
        return (
            "Soumis : écoutez, puis publiez ou renvoyez au rédacteur."
            if user.is_content_manager
            else "En relecture : plus modifiable, sauf s'il vous est renvoyé."
        )
    return "Publié : c'est la version audio que les apprenants écoutent."


@author_required
@require_POST
def audio_part(request, pk):
    """Ajouter, renommer, déplacer ou retirer un chapitre enregistré."""
    upload = _upload(request, pk)
    if not editorial.can_edit(request.user, upload):
        messages.error(request, "Cet enregistrement n'est pas modifiable en ce moment.")
        return redirect(_url(upload))
    verb = request.POST.get("action", "ajouter")
    if verb == "ajouter":
        form = PartForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
            return redirect(_url(upload, "#ajouter"))
        position = (upload.parts.aggregate(n=Max("position"))["n"] or 0) + 1
        part = AudioUploadPart.objects.create(
            upload=upload,
            position=position,
            title=form.cleaned_data["title"],
            audio=form.cleaned_data["audio"],
            duration_seconds=form.duration,
        )
        minutes, seconds = divmod(int(form.duration), 60)
        messages.success(request, f"Chapitre « {part.title} » ajouté ({minutes}:{seconds:02d}).")
        return redirect(_url(upload, f"#p{part.pk}"))
    part = get_object_or_404(AudioUploadPart, pk=request.POST.get("part"), upload=upload)
    parts = list(upload.parts.all())
    if verb == "renommer":
        title = request.POST.get("title", "").strip()
        if title:
            part.title = title[:255]
            part.save(update_fields=["title"])
    elif verb == "retirer":
        if not upload.published_at:
            part.audio.delete(save=False)
        part.delete()
        parts = [p for p in parts if p.pk != part.pk]
    elif verb in ("monter", "descendre"):
        index = next(i for i, p in enumerate(parts) if p.pk == part.pk)
        target = index - 1 if verb == "monter" else index + 1
        if 0 <= target < len(parts):
            parts.insert(target, parts.pop(index))
    with transaction.atomic():
        for n, item in enumerate(parts, start=1):
            if item.position != n:
                AudioUploadPart.objects.filter(pk=item.pk).update(position=n)
    return redirect(_url(upload, f"#p{part.pk}" if verb != "retirer" else "#chapitres"))


@author_required
@require_POST
def audio_action(request, pk):
    upload = _upload(request, pk)
    user, verb = request.user, request.POST.get("action")
    if (
        verb == "soumettre"
        and editorial.can_edit(user, upload)
        and upload.editorial_status == editorial.WRITING
    ):
        found = audio.problems(upload)
        if found:
            messages.error(request, "Avant de soumettre : " + " ; ".join(found) + ".")
            return redirect(_url(upload))
        editorial.submit(upload)
        messages.success(
            request,
            "Enregistrement soumis à la relecture : un gestionnaire l'écoutera puis le publiera.",
        )
    elif verb == "publier" and user.is_content_manager and editorial.is_draft(upload):
        found = audio.problems(upload)
        if found:
            messages.error(request, "Avant de publier : " + " ; ".join(found) + ".")
            return redirect(_url(upload))
        audio.publish(upload)
        messages.success(
            request,
            f"« {upload.guide.title} » : votre enregistrement remplace la voix de synthèse.",
        )
    elif (
        verb == "renvoyer"
        and user.is_content_manager
        and upload.editorial_status == editorial.REVIEW
    ):
        editorial.send_back(upload)
        messages.info(request, "Enregistrement renvoyé à son rédacteur.")
    elif verb == "supprimer" and editorial.deletable(user, upload) and not upload.published_at:
        for part in upload.parts.all():
            part.audio.delete(save=False)
        upload.delete()
        messages.info(request, "Enregistrement supprimé.")
        return redirect("studio:list", "audio")
    else:
        messages.error(request, "Action impossible en ce moment.")
    return redirect(_url(upload))


@author_required
def audio_file(request, pk, part):
    """Écoute d'un chapitre déposé, pendant l'écriture et la relecture (les médias ne sont pas
    publics)."""
    from catalogue.views import _serve_range  # import local : catalogue.views est lourd à charger
    upload = _upload(request, pk)
    item = get_object_or_404(AudioUploadPart, pk=part, upload=upload)
    return _serve_range(request, item.audio, "audio/mpeg")
