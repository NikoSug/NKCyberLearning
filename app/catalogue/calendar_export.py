"""
Planning de lecture au format iCalendar (RFC 5545) : un fichier .ics à importer dans Thunderbird,
Google Agenda,
Proton Calendar ou Outlook. Un rendez-vous par jour de lecture, à l'heure choisie dans le planning.

Le fichier est un instantané : si le planning change, on l'importe à nouveau. Chaque rendez-vous
garde le même
identifiant (UID) d'un export à l'autre, ce qui permet aux agendas qui le gèrent de le mettre à jour
au lieu de
le dupliquer.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone

MAX_DAYS = 120          # jours de lecture exportés au plus (le planning peut courir sur des mois)
MIN_MINUTES = 15


def _escape(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """Lignes d'au plus 75 octets, continuées par une espace (RFC 5545, § 3.1)."""
    raw, out, current = line.encode("utf-8"), [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current)
            current = b""
        current += encoded
    out.append(current)
    return "\r\n ".join(part.decode("utf-8") for part in out) if len(raw) > 75 else line


def _utc(moment):
    return moment.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    days, reading_time, minutes_per_day, url_for, planning_url, uid_owner, uid_domain, instance_name
):
    """
    Fichier iCalendar des `days` (planning.Day) ; `url_for(document)` donne l'adresse absolue d'un
    document,
    `planning_url` celle du planning ; identifiants « <uid_owner>-<date>@<uid_domain> », propres à
    l'utilisateur
    et à l'instance.
    """
    now = _utc(timezone.now())
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{instance_name}//Planning de lecture//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(instance_name)} — lectures",
    ]
    for day in days[:MAX_DAYS]:
        start = timezone.make_aware(datetime.combine(day.date, reading_time))
        # Un guide long réparti sur plusieurs jours : chaque rendez-vous dure le temps prévu par
        # jour, pas plus.
        end = start + timedelta(minutes=max(MIN_MINUTES, min(day.minutes, minutes_per_day)))
        if day.continued is not None:
            documents, summary = [
                day.continued.document
            ], f"Lecture : suite de « {day.continued.document.title} »"
        else:
            documents = [item.document for item in day.items]
            summary = (f"Lecture : « {documents[0].title} »" if len(documents) == 1
                       else f"Lecture : {len(documents)} documents")
        description = "\n".join([f"- {doc.title} : {url_for(doc)}" for doc in documents]
                                + ["", f"Mon planning : {planning_url}"])
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid_owner}-{day.date:%Y%m%d}@{uid_domain}",
            f"DTSTAMP:{now}",
            f"DTSTART:{_utc(start)}",
            f"DTEND:{_utc(end)}",
            f"SUMMARY:{_escape(f'📖 {summary}')}",
            f"DESCRIPTION:{_escape(description)}",
            f"URL:{url_for(documents[0])}",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
