"""
Versions audio enregistrées par un rédacteur : un fichier MP3 par chapitre. À la publication, elles
remplacent la version audio du guide (voix de synthèse) et sont servies par le même lecteur.
"""

from django.db import transaction
from django.utils import timezone

from catalogue import narration_text
from catalogue.models import Narration, NarrationSection

from . import editorial

# En-tête de trame MP3 (MPEG 1/2/2.5, couches I à III) : débits (kbit/s) et fréquences
# d'échantillonnage.
BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}
MAX_BYTES = 55 * 1024 * 1024  # nginx accepte 60 Mo par envoi (client_max_body_size)


def _skip_id3(data):
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        return 10 + size
    return 0


def mp3_duration(data):
    """Durée (s) d'un MP3 en parcourant ses trames ; None si ce n'est pas un MP3 lisible."""
    position, seconds, frames = _skip_id3(data), 0.0, 0
    while position + 4 <= len(data):
        header = data[position:position + 4]
        if header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
            position += 1
            if frames == 0 and position > 64 * 1024:
                return None
            continue
        version_bits, layer_bits = (header[1] >> 3) & 3, (header[1] >> 1) & 3
        bitrate_index, rate_index, padding = (
            header[2] >> 4,
            (header[2] >> 2) & 3,
            (header[2] >> 1) & 1,
        )
        if version_bits == 1 or layer_bits == 0 or bitrate_index in (0, 15) or rate_index == 3:
            position += 1
            continue
        version = {3: 1, 2: 2, 0: 25}[version_bits]
        layer = 4 - layer_bits
        bitrate = BITRATES[(1 if version == 1 else 2, layer)][bitrate_index] * 1000
        rate = RATES[version][rate_index]
        if layer == 1:
            samples, length = 384, (12 * bitrate // rate + padding) * 4
        else:
            samples = 1152 if layer == 2 or version == 1 else 576
            length = samples // 8 * bitrate // rate + padding
        if length <= 0:
            position += 1
            continue
        seconds += samples / rate
        frames += 1
        position += length
    return round(seconds, 1) if frames >= 2 else None


def problems(upload):
    parts = list(upload.parts.all())
    found = [] if parts else ["ajouter au moins un chapitre enregistré"]
    found += [f"chapitre {p.position} sans titre" for p in parts if not p.title.strip()]
    return found


@transaction.atomic
def publish(upload):
    """Remplace la version audio du guide par l'enregistrement (les fichiers restent ceux de
    l'envoi)."""
    narration, _ = Narration.objects.get_or_create(guide=upload.guide)
    for section in narration.sections.all():
        if not section.audio.name.startswith("narrations/fournies/"):
            section.audio.delete(save=False)  # ancienne voix de synthèse : plus utile
    narration.sections.all().delete()
    parts = list(upload.parts.order_by("position"))
    for n, part in enumerate(parts, start=1):
        NarrationSection.objects.create(narration=narration, position=n, title=part.title[:255],
                                        text="(enregistrement)", audio=part.audio.name,
                                        duration_seconds=part.duration_seconds)
    author = upload.author.username if upload.author else "un rédacteur"
    narration.status, narration.version, narration.voice = (
        Narration.Status.READY,
        narration_text.VERSION,
        f"Enregistrement de {author}",
    )
    narration.sections_total = narration.sections_done = len(parts)
    narration.duration_seconds = round(sum(p.duration_seconds for p in parts), 1)
    narration.finished_at, narration.error, narration.queue_rank = timezone.now(), "", None
    narration.save()
    upload.published_at = timezone.now()
    upload.save(update_fields=["published_at"])
    editorial.publish(upload)
    return narration
