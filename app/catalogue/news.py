"""
Actualités cyber : récupération des flux RSS publics affichés sur le tableau de bord.

Sécurité :
  - seuls les domaines des sources listées ci-dessous sont contactés, en HTTPS,
    redirections comprises, avec une taille et une durée maximales ;
  - le XML est lu avec defusedxml (pas d'entités externes ni d'expansion abusive) ;
  - on ne garde que du texte brut et des liens http(s) : aucun HTML externe n'est
    rendu dans l'application.
"""
import html
import re
import urllib.request
from dataclasses import dataclass
from datetime import timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from urllib.parse import urlparse

from defusedxml import ElementTree
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import NewsItem

Kind = NewsItem.Kind


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    url: str
    kind: str
    site: str


SOURCES = [
    Source(
        "certfr-alertes",
        "CERT-FR",
        "https://www.cert.ssi.gouv.fr/alerte/feed/",
        Kind.ALERT,
        "https://www.cert.ssi.gouv.fr/",
    ),
    Source(
        "certfr-actualites",
        "CERT-FR",
        "https://www.cert.ssi.gouv.fr/actualite/feed/",
        Kind.NEWS,
        "https://www.cert.ssi.gouv.fr/",
    ),
    Source(
        "cybermalveillance",
        "Cybermalveillance.gouv.fr",
        "https://www.cybermalveillance.gouv.fr/feed/atom-flux-actualites",
        Kind.NEWS,
        "https://www.cybermalveillance.gouv.fr/",
    ),
    Source("zataz", "ZATAZ", "https://www.zataz.com/feed/", Kind.NEWS, "https://www.zataz.com/"),
    Source(
        "lmi-securite",
        "Le Monde Informatique",
        "https://www.lemondeinformatique.fr/flux-rss/thematique/securite/rss.xml",
        Kind.NEWS,
        "https://www.lemondeinformatique.fr/securite/",
    ),
    Source(
        "bonjourlafuite",
        "C'est qui qui a fuité aujourd'hui ?",
        "https://bonjourlafuite.eu.org/feed.xml",
        Kind.LEAK,
        "https://bonjourlafuite.eu.org/",
    ),
]
SOURCE_NAMES = {s.key: s.name for s in SOURCES}
ALLOWED_HOSTS = {urlparse(s.url).hostname for s in SOURCES}

USER_AGENT = "CyberLearning-actualites/1.0 (plateforme de formation interne)"
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
KEEP_DAYS = 90
SUMMARY_LENGTH = 320
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"


class FeedError(Exception):
    pass


def _check_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise FeedError(f"URL refusée : {url}")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirects)


def download(url):
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _opener.open(
            request, timeout=TIMEOUT
        ) as response:  # nosec B310 (URL contrôlée ci-dessus)
            data = response.read(MAX_BYTES + 1)
    except FeedError:
        raise
    except Exception as exc:
        raise FeedError(f"{url} : {exc}") from exc
    if len(data) > MAX_BYTES:
        raise FeedError(f"{url} : flux trop volumineux")
    return data


# --- Lecture des flux --------------------------------------------------------------
TAGS = re.compile(r"<[^>]+>")
SCRIPTS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
SPACES = re.compile(r"\s+")
LIST_ITEMS = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)


def plain_text(value, limit=None):
    """Texte brut à partir d'un fragment HTML (balises retirées, entités décodées)."""
    text = SPACES.sub(" ", html.unescape(TAGS.sub(" ", SCRIPTS.sub(" ", value or "")))).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def safe_link(url):
    url = (url or "").strip()
    return url if urlparse(url).scheme in ("http", "https") else ""


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        date = parsedate_to_datetime(value)  # RSS : RFC 822
    except (TypeError, ValueError):
        date = parse_datetime(value)  # Atom : ISO 8601
    if date and timezone.is_naive(date):
        date = date.replace(tzinfo=dt_timezone.utc)
    return date


def _text(node, tag):
    child = node.find(tag)
    return (child.text or "") if child is not None else ""


def parse_feed(data):
    """Entrées d'un flux RSS 2.0, RSS 1.0 (RDF) ou Atom : title, link, guid, date, description,
    categories."""
    try:
        root = ElementTree.fromstring(data)
    except Exception as exc:  # XML invalide ou construction refusée par defusedxml
        raise FeedError(f"flux illisible : {exc}") from exc
    entries = []
    for ns in ("", RSS1):  # RSS 2.0 (sans espace de noms) puis RSS 1.0
        for item in root.iter(f"{ns}item"):
            entries.append({
                "title": _text(item, f"{ns}title"), "link": _text(item, f"{ns}link"),
                "guid": _text(item, "guid") or _text(item, f"{ns}link"),
                "date": parse_date(_text(item, "pubDate") or _text(item, f"{DC}date")),
                "description": _text(item, f"{ns}description"),
                "categories": [c.text or "" for c in item.findall("category")],
            })
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        entries.append(
            {
                "title": _text(entry, f"{ATOM}title"),
                "link": link.get("href", "") if link is not None else "",
                "guid": _text(entry, f"{ATOM}id"),
                "date": parse_date(
                    _text(entry, f"{ATOM}updated") or _text(entry, f"{ATOM}published")
                ),
                "description": _text(entry, f"{ATOM}summary") or _text(entry, f"{ATOM}content"),
                "categories": [c.get("term", "") for c in entry.findall(f"{ATOM}category")],
            }
        )
    return entries


LEAK_STATUS = {"🟢": True, "🟠": False, "🟡": False, "🔴": False}


def to_item(source, entry):
    """Transforme une entrée de flux en NewsItem (non enregistré), ou None si elle est
    inutilisable."""
    title, link = plain_text(entry["title"]), safe_link(entry["link"])
    if not title or not link:
        return None
    item = NewsItem(source=source.key, kind=source.kind, url=link[:500],
                    published_at=entry["date"] or timezone.now(),
                    guid=sha256((entry["guid"] or link).encode()).hexdigest())
    if source.kind == Kind.LEAK:
        # Titre « 🟢 Organisme » : la pastille indique si la fuite est confirmée.
        marker, _, name = title.partition(" ")
        if marker in LEAK_STATUS:
            item.confirmed, title = LEAK_STATUS[marker], name.strip() or title
        item.details = [plain_text(li, 80) for li in LIST_ITEMS.findall(entry["description"])][:12]
        item.sensitive = "sensitive" in entry["categories"]
    else:
        item.summary = plain_text(entry["description"], SUMMARY_LENGTH)
    item.title = title[:300]
    return item


def refresh_source(source):
    """Récupère un flux et enregistre ses entrées. Renvoie le nombre d'entrées lues."""
    entries = parse_feed(download(source.url))
    items = list({i.guid: i for i in (to_item(source, e) for e in entries) if i}.values())
    # Les entrées déjà connues sont mises à jour (une fuite peut passer de 🟠 à 🟢).
    NewsItem.objects.bulk_create(
        items,
        update_conflicts=True,
        unique_fields=["source", "guid"],
        update_fields=[
            "title",
            "url",
            "summary",
            "details",
            "confirmed",
            "sensitive",
            "fetched_at",
        ],
    )
    return len(items)


def refresh_all(sources=tuple(SOURCES)):
    """Rafraîchit toutes les sources ; une source en panne n'empêche pas les autres."""
    report = {}
    for source in sources:
        try:
            report[source.key] = refresh_source(source)
        except FeedError as exc:
            report[source.key] = exc
    NewsItem.objects.filter(published_at__lt=timezone.now() - timedelta(days=KEEP_DAYS)).delete()
    return report


def latest(kind, limit, per_source=None, max_age_days=None):
    """Dernières entrées d'un type ; `per_source` évite qu'une source très active occupe toute la
    liste."""
    items = NewsItem.objects.filter(kind=kind).order_by("-published_at")
    if max_age_days:
        items = items.filter(published_at__gte=timezone.now() - timedelta(days=max_age_days))
    if not per_source:
        return list(items[:limit])
    chosen, counts = [], {}
    for item in items[:limit * 10]:
        if counts.get(item.source, 0) < per_source:
            chosen.append(item)
            counts[item.source] = counts.get(item.source, 0) + 1
            if len(chosen) == limit:
                break
    return chosen


def last_refresh():
    item = NewsItem.objects.order_by("-fetched_at").first()
    return item.fetched_at if item else None
