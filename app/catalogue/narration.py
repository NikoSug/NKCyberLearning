"""
Versions audio des guides : file d'attente, synthèse vocale et encodage.

  - Un utilisateur demande la version audio d'un guide : elle entre dans la file
    (priorité 0 ; une génération en masse passe après, priorité 1).
  - Le service « narrator » (commande `narrate --boucle`) prend les demandes une à une,
    extrait le texte (narration_text), le fait réécrire pour l'oral par l'IA locale
    (narration_script), puis l'envoie au service « tts » (Kyutai TTS, sur le réseau interne)
    phrase par phrase et encode chaque section en MP3.

Le modèle de synthèse vocale n'est jamais importé : il tourne dans son propre conteneur (GPU)
et ne communique qu'en HTTP.
"""
import array
import hashlib
import http.client
import io
import json
import time
import urllib.error
import urllib.request
import wave
from datetime import timedelta

import lameenc
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from . import generation, narration_script, narration_text
from .models import Guide, Language, Narration, NarrationSection

Status = Narration.Status
USER_PRIORITY, BULK_PRIORITY = 0, 1
MP3_BITRATE = 96          # kbit/s, mono : voix naturelle sans artefacts (≈ 720 Ko par minute)
CHUNK_LIMIT = 400         # caractères par appel : Kyutai lit mieux des passages courts
CHUNK_PAUSE_SECONDS = 0.25
DIALOGUE_LIMIT = 600       # caractères par groupe de répliques (podcasts)
DIALOGUE_TURNS = 4
TARGET_PEAK = 0.89        # normalisation : crête à environ -1 dB
RETRY_DELAYS = (
    5,
    15,
    30,
    60,
    90,
)  # secondes : carte saturée ou service de voix qui redémarre (≈ 3 min)
STALE_AFTER = timedelta(hours=2)


class NarrationError(Exception):
    pass


# --- File d'attente ---------------------------------------------------------------------
def source_document(guide):
    return guide.documents.exclude(file="").order_by("position", "pk").first()


def is_current(narration):
    return narration.status == Status.READY and narration.version == narration_text.VERSION


def outdated_guides(guides):
    """Guides dont la version audio prête date d'un système précédent (voix, nettoyage du texte) : à
    régénérer."""
    return guides.filter(
        narration__status=Status.READY, narration__version__lt=narration_text.VERSION
    )


def request(guide, user=None, priority=USER_PRIORITY):
    """Demande la version audio d'un guide (sans effet si elle est prête ou déjà demandée)."""
    if not settings.AI_ENABLED:
        raise NarrationError("La synthèse vocale n'est pas activée sur cette installation.")
    if source_document(guide) is None:
        raise NarrationError("Ce guide n'a pas de document PDF à lire.")
    narration, created = Narration.objects.get_or_create(
        guide=guide, defaults={"priority": priority, "requested_by": user})
    if created or is_current(narration) or narration.status == Status.PROCESSING:
        return narration
    if narration.status == Status.PENDING:
        if (
            priority < narration.priority
        ):  # un utilisateur l'attend : elle passe devant la génération en masse
            narration.priority = priority
            narration.save(update_fields=["priority"])
        return narration
    # Échec ou version périmée (nettoyage du texte amélioré depuis) : on régénère.
    narration.status, narration.priority, narration.error = Status.PENDING, priority, ""
    narration.requested_at, narration.requested_by = timezone.now(), user or narration.requested_by
    narration.save(update_fields=["status", "priority", "error", "requested_at", "requested_by"])
    return narration


def request_all(user=None):
    """Met en file tous les guides en français avec un PDF (après les demandes des utilisateurs)."""
    queued = 0
    for guide in Guide.objects.filter(is_published=True, language=Language.FR).order_by("title"):
        try:
            request(guide, user, priority=BULK_PRIORITY)
            queued += 1
        except NarrationError:
            pass  # pas de PDF
    return queued


def queue_position(narration):
    """Rang dans la file (1 = la prochaine), ou 0 si elle n'attend pas."""
    if narration.status != Status.PENDING:
        return 0
    order = list(Narration.objects.filter(status=Status.PENDING).order_by(*Narration.QUEUE_ORDER)
                 .values_list("pk", flat=True))
    return order.index(narration.pk) + 1 if narration.pk in order else 0


def recover_stale(immediate=False):
    """
    Remet en file les générations interrompues (service arrêté en plein travail).
    `immediate` : au démarrage du service « narrator », le seul à générer, tout ce qui est
    « en cours » a forcément été interrompu ; sinon, seulement les générations anciennes.
    """
    stale = Narration.objects.filter(status=Status.PROCESSING)
    if not immediate:
        stale = stale.filter(started_at__lt=timezone.now() - STALE_AFTER)
    return stale.update(status=Status.PENDING)


def take_next():
    """Réserve la prochaine demande (sûr même avec plusieurs services de génération)."""
    with transaction.atomic():
        narration = (Narration.objects.select_for_update(skip_locked=True)
                     .filter(status=Status.PENDING).order_by(*Narration.QUEUE_ORDER).first())
        if narration is None:
            return None
        narration.status, narration.started_at = Status.PROCESSING, timezone.now()
        narration.sections_done = narration.sections_total = 0
        narration.save(update_fields=["status", "started_at", "sections_done", "sections_total"])
        return narration


# --- Synthèse et encodage --------------------------------------------------------------------
def _tts_url(path):
    return settings.NARRATION_TTS_URL.rstrip("/") + path


def voice_name():
    try:
        with urllib.request.urlopen(
            _tts_url("/info"), timeout=10
        ) as response:  # nosec B310 (URL de configuration)
            return json.load(response)["voice"]["name"]
    except Exception:
        return ""


def synthesize(text, checkpoint=None):
    """
    Texte → audio PCM 16 bits mono (octets, fréquence d'échantillonnage).
    `checkpoint` est appelé avant chaque morceau (quelques secondes) : la pause agit vite.
    """
    return _synthesize_all(
        [{"text": piece} for piece in narration_text.chunks(text, CHUNK_LIMIT)], checkpoint
    )


def dialogue_groups(turns, limit=DIALOGUE_LIMIT, max_turns=DIALOGUE_TURNS):
    """
    Répliques [{speaker, text}] → petits groupes lus d'un trait à deux voix (l'enchaînement des
    tours
    de parole reste naturel) ; une longue réplique est d'abord coupée entre ses phrases.
    """
    pieces = [{"speaker": turn["speaker"], "text": piece}
              for turn in turns for piece in narration_text.chunks(turn["text"], CHUNK_LIMIT)]
    groups, current, size = [], [], 0
    for piece in pieces:
        if current and (size + len(piece["text"]) > limit or len(current) >= max_turns):
            groups.append(current)
            current, size = [], 0
        current.append(piece)
        size += len(piece["text"])
    if current:
        groups.append(current)
    return groups


def synthesize_dialogue(turns, checkpoint=None):
    """Dialogue à deux voix (podcasts) → audio PCM 16 bits mono, comme `synthesize`."""
    return _synthesize_all([{"turns": group} for group in dialogue_groups(turns)], checkpoint)


def _synthesize_all(payloads, checkpoint=None):
    pcm, rate = io.BytesIO(), None
    for payload in payloads:
        if checkpoint:
            checkpoint()
        data = _synthesize_piece(payload)
        with wave.open(io.BytesIO(data)) as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise NarrationError("format audio inattendu renvoyé par la synthèse vocale")
            if rate not in (None, wav.getframerate()):
                raise NarrationError("fréquence d'échantillonnage incohérente")
            rate = wav.getframerate()
            pcm.write(wav.readframes(wav.getnframes()))
        pcm.write(b"\x00\x00" * int(rate * CHUNK_PAUSE_SECONDS))  # courte pause entre deux morceaux
    if rate is None:
        raise NarrationError("aucun texte à lire")
    return normalize(pcm.getvalue()), rate


def _synthesize_piece(payload):
    """
    Un passage court → WAV. Carte graphique pleine (IA relancée entre-temps) ou service de voix qui
    redémarre après une erreur de la carte : on libère l'IA, on attend et on réessaie (≈ 3 minutes).
    """
    body = json.dumps(payload).encode("utf-8")
    for delay in (*RETRY_DELAYS, None):
        generation.voice_busy()  # l'assistant se met en pause pendant la synthèse
        req = urllib.request.Request(_tts_url("/synthesize"), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(
                req, timeout=300
            ) as response:  # nosec B310 (URL de configuration)
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 503 or delay is None:
                raise NarrationError(
                    f"synthèse vocale en erreur ({exc.code}) : {exc.read()[:200]!r}"
                ) from exc
            generation.unload_model()
        except (
            urllib.error.URLError,
            ConnectionError,
            TimeoutError,
            http.client.HTTPException,
        ) as exc:
            if delay is None:  # service toujours injoignable après plusieurs minutes
                raise NarrationError(f"service de synthèse vocale injoignable : {exc}") from exc
        time.sleep(delay)


def normalize(pcm):
    """Volume homogène d'un chapitre à l'autre (crête ramenée à TARGET_PEAK), PCM 16 bits."""
    samples = array.array("h", pcm)
    peak = max(map(abs, samples), default=0)
    if peak < 32:
        return pcm
    gain = TARGET_PEAK * 32767 / peak
    return array.array("h", (max(-32767, min(32767, round(x * gain))) for x in samples)).tobytes()


def release_voice():
    """Décharge le modèle de synthèse vocale de la carte graphique (il se rechargera à la
    demande)."""
    generation.voice_busy(False)  # l'assistant peut reprendre
    req = urllib.request.Request(
        _tts_url("/unload"), data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30):  # nosec B310 (URL de configuration)
            return True
    except Exception:
        return False  # service arrêté : rien à libérer


def encode_mp3(pcm, rate):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_BITRATE)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(1)
    encoder.set_quality(2)  # 2 = haute qualité (0 meilleur, 7 plus rapide)
    return bytes(encoder.encode(pcm) + encoder.flush())


def _source_hash(section):
    """Empreinte du texte d'origine d'un chapitre (et des règles d'adaptation) : réécriture
    réutilisable ?"""
    raw = f"{narration_text.VERSION}\n{section.title}\n{section.text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _has_audio(item):
    return bool(item.audio) and item.audio.storage.exists(item.audio.name)


def _set_done(narration, done):
    narration.sections_done = done
    narration.save(update_fields=["sections_done"])


def generate(narration, progress=None, checkpoint=None):
    """
    Produit toutes les sections audio d'un guide. Lève NarrationError en cas d'échec.

    Deux phases, pour ne jamais avoir les deux modèles ensemble sur une carte graphique de 8 Go :
      1. chaque chapitre est réécrit pour l'oral par l'IA locale (texte enregistré aussitôt) ;
      2. l'IA est déchargée, puis Kyutai lit les chapitres un à un (MP3 enregistré aussitôt).
    `checkpoint` est appelé souvent (pause) ; tout ce qui est déjà fait est gardé, une génération
    interrompue reprend donc où elle s'était arrêtée. La progression compte les deux phases.
    """
    document = source_document(narration.guide)
    if document is None:
        raise NarrationError("plus de document PDF pour ce guide")
    if not document.file.storage.exists(document.file.name):
        raise NarrationError(f"PDF introuvable par le service ({document.file.name}) : "
                             "le volume des médias est-il monté dans le conteneur ?")
    with document.file.open("rb") as pdf:
        sections = narration_text.extract_sections(pdf, spoken=False)
    if not sections:
        raise NarrationError("aucun texte lisible dans le PDF (document scanné ?)")

    count = len(sections)
    existing = {old.position: old for old in narration.sections.all()}
    for position in [p for p in existing if p > count]:
        existing.pop(position).audio.delete(save=False)
    narration.sections.filter(position__gt=count).delete()
    narration.sections_total = 2 * count
    narration.save(update_fields=["sections_total"])

    try:
        # 1. Réécriture pour l'oral (IA locale)
        items, known = [], set()
        for position, section in enumerate(sections, start=1):
            if checkpoint:
                checkpoint()
            source = _source_hash(section)
            item = existing.get(position)
            if item is None or item.source_hash != source or item.title != section.title[:255]:
                if item is not None:
                    item.audio.delete(save=False)
                    item.delete()
                text = narration_script.rewrite(section, known, checkpoint)
                item = NarrationSection.objects.create(
                    narration=narration,
                    position=position,
                    title=section.title[:255],
                    text=text,
                    source_hash=source,
                )
            known.update(narration_script.acronyms(item.text))
            items.append(item)
            _set_done(narration, position)
            if progress:
                progress(position, 2 * count, f"réécriture : {section.title}")

        # 2. Lecture par la synthèse vocale : l'IA libère d'abord la carte graphique
        generation.unload_model()
        total = 0.0
        for item in items:
            if checkpoint:
                checkpoint()
            if not _has_audio(item):
                pcm, rate = synthesize(item.text, checkpoint)
                item.duration_seconds = round(len(pcm) / 2 / rate, 1)
                item.audio.save(f"{narration.guide.slug}-{item.position:02d}.mp3",
                                ContentFile(encode_mp3(pcm, rate)), save=False)
                item.save()
            total += item.duration_seconds
            _set_done(narration, count + item.position)
            if progress:
                progress(count + item.position, 2 * count, f"lecture : {item.title}")
    finally:
        release_voice()  # la carte graphique redevient disponible (IA, jeux)

    narration.status, narration.finished_at = Status.READY, timezone.now()
    narration.duration_seconds, narration.version = round(total, 1), narration_text.VERSION
    narration.voice, narration.error = voice_name(), ""
    narration.save()
    return narration


def suspend(narration):
    """Pause demandée : retour en tête de file, chapitres déjà produits conservés."""
    narration.status = Status.PENDING
    narration.save(update_fields=["status"])


def fail(narration, error):
    narration.status, narration.error, narration.finished_at = (
        Status.FAILED,
        str(error)[:2000],
        timezone.now(),
    )
    narration.save(update_fields=["status", "error", "finished_at"])
