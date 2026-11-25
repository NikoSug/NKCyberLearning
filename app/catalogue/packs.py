"""
Packs de contenus : tout le contenu publié d'une instance (guides et PDF, index de recherche,
référentiels,
certifications, QCM, lexiques, versions audio, parcours, cours, podcasts), sans aucune donnée
personnelle
(comptes, progression, lectures, tests, historique). Sert à installer une instance déjà remplie et à
la mettre
à jour ensuite.

Archive tar, écrite et lue en flux (pas de copie temporaire de plusieurs Go) :
    media/<sha256><ext>   fichiers (PDF, MP3, images), nommés par leur empreinte : vérifiés à la
    lecture
    data.json             contenus par type, dans l'ordre d'import
    manifest.json         format, version, origine, migrations requises, empreinte de data.json,
    compteurs

Import (mise à jour sûre) :
  - un contenu existant est mis à jour sur place (mêmes identifiants) : progression, tentatives et
  écoutes des
    utilisateurs restent valables ;
  - un contenu modifié sur l'instance depuis son import n'est pas écrasé (empreintes, voir
  ImportedContent),
    ni un contenu écrit par un rédacteur de l'instance ; « forcer » passe outre ;
  - rien n'est supprimé : un contenu retiré du pack reste en place.
"""

import hashlib
import json
import posixpath
import tarfile
from dataclasses import dataclass, field

from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

FORMAT = 1
MEDIA_DIR = "contenus"            # fichiers importés, rangés par empreinte dans MEDIA_ROOT
CONTENT_APPS = ("catalogue", "courses", "podcasts")
MAX_MEMBER_BYTES = 600 * 1024 * 1024
EDITORIAL = {"author": None, "editorial_status": "", "submitted_at": None, "is_published": True}
GENERATED = {"author": None, "requested_by": None, "error": "", "progress": ""}
VISIBILITY = (
    "is_published",
    "editorial_status",
    "status",
)  # changés ici = choix de l'instance, respecté


class PackError(Exception):
    pass


@dataclass
class Child:
    name: str
    model: str
    parent: str                      # clé étrangère vers le contenu principal
    key: tuple                       # champs qui identifient l'élément dans son contenu
    fields: tuple
    files: tuple = ()
    refs: dict = field(default_factory=dict)  # champ -> type de contenu référencé (par son slug)
    siblings: dict = field(default_factory=dict)  # champ -> nom d'un autre élément du même contenu
    fixed: dict = field(default_factory=dict)


@dataclass
class Spec:
    kind: str
    label: str
    model: str
    key: str  # « slug », ou champ de référence (lexique, audio : par guide)
    roots: object                    # fonction -> contenus à exporter
    fields: tuple = ()
    files: tuple = ()
    refs: dict = field(default_factory=dict)
    m2m: dict = field(default_factory=dict)
    fixed: dict = field(default_factory=dict)
    children: tuple = ()
    attach: bool = (
        False  # rattaché à un guide existant (QCM, index) : le guide n'est ni créé ni modifié
    )


def _model(path):
    return apps.get_model(path)


def _published_guides():
    return _model("catalogue.Guide").objects.filter(is_published=True, editorial_status="")


SPECS = (
    Spec(
        "domaine",
        "Domaines",
        "catalogue.GuideCategory",
        "slug",
        lambda: _model("catalogue.GuideCategory").objects.all(),
        fields=("name", "description", "position"),
    ),
    Spec(
        "guide",
        "Guides",
        "catalogue.Guide",
        "slug",
        _published_guides,
        fields=(
            "anssi_id",
            "title",
            "collection",
            "theme",
            "needs",
            "language",
            "level",
            "page_count",
            "estimated_minutes",
            "summary",
            "version",
            "published_on",
            "source_url",
        ),
        files=("cover",),
        refs={"category": "domaine"},
        fixed=EDITORIAL,
        children=(
            Child(
                "documents",
                "catalogue.GuideDocument",
                "guide",
                ("position",),
                ("label", "source_url", "page_count", "position"),
                files=("file",),
            ),
        ),
    ),
    Spec(
        "index",
        "Index de recherche",
        "catalogue.Guide",
        "slug",
        lambda: _published_guides().filter(text_sections__isnull=False).distinct(),
        attach=True,
        children=(
            Child(
                "sections",
                "catalogue.GuideSection",
                "guide",
                ("position",),
                ("position", "title", "text"),
            ),
        ),
    ),
    Spec(
        "referentiel",
        "Référentiels et programmes",
        "catalogue.Referential",
        "slug",
        lambda: _model("catalogue.Referential").objects.filter(
            is_published=True, editorial_status=""
        ),
        fields=(
            "name",
            "short_name",
            "kind",
            "version",
            "issuer",
            "description",
            "source_url",
            "grid_source",
            "page_count",
            "estimated_minutes",
            "trame_label",
        ),
        files=("document", "trame"),
        fixed=EDITORIAL,
        children=(
            Child(
                "exigences",
                "catalogue.Requirement",
                "referential",
                ("position",),
                ("kind", "ref", "theme", "text", "guidance", "evidence", "weight", "position"),
                siblings={"parent": "exigences"},
            ),
            Child(
                "citations",
                "catalogue.GuideCitation",
                "referential",
                ("position",),
                ("code", "title", "position"),
                refs={"guide": "guide"},
            ),
        ),
    ),
    Spec(
        "certification",
        "Certifications",
        "catalogue.Certification",
        "slug",
        lambda: _model("catalogue.Certification").objects.filter(
            is_published=True, editorial_status=""
        ),
        fields=(
            "name",
            "acronym",
            "kind",
            "issuer",
            "domain",
            "level",
            "audience",
            "prerequisites",
            "exam_format",
            "validity",
            "description",
            "source_url",
        ),
        m2m={"referentials": "referentiel", "guide_categories": "domaine"},
        fixed=EDITORIAL,
    ),
    Spec(
        "qcm",
        "QCM",
        "catalogue.Guide",
        "slug",
        lambda: _published_guides().filter(quiz_questions__isnull=False).distinct(),
        attach=True,
        children=(
            Child(
                "questions",
                "catalogue.QuizQuestion",
                "guide",
                ("series", "position"),
                (
                    "series",
                    "position",
                    "text",
                    "choices",
                    "answer",
                    "explanation",
                    "source",
                    "diagram",
                ),
            ),
        ),
    ),
    Spec(
        "lexique",
        "Lexiques",
        "catalogue.Glossary",
        "guide",
        lambda: _model("catalogue.Glossary").objects.filter(
            status="publie", guide__in=_published_guides()
        ),
        fields=("llm_model", "generated_at", "published_at"),
        refs={"guide": "guide"},
        fixed={**GENERATED, "status": "publie", "published_by": None},
        children=(
            Child(
                "entrees",
                "catalogue.GlossaryEntry",
                "glossary",
                ("key",),
                ("term", "key", "kind", "expansion", "definition"),
            ),
        ),
    ),
    Spec(
        "audio",
        "Versions audio",
        "catalogue.Narration",
        "guide",
        lambda: _model("catalogue.Narration").objects.filter(
            status="prete", guide__in=_published_guides()
        ),
        fields=(
            "voice",
            "version",
            "sections_total",
            "sections_done",
            "duration_seconds",
            "finished_at",
        ),
        refs={"guide": "guide"},
        fixed={"status": "prete", "error": "", "requested_by": None},
        children=(
            Child(
                "sections",
                "catalogue.NarrationSection",
                "narration",
                ("position",),
                ("position", "title", "text", "source_hash", "duration_seconds"),
                files=("audio",),
            ),
        ),
    ),
    Spec(
        "parcours",
        "Parcours sur mesure",
        "catalogue.ReadingPath",
        "slug",
        lambda: _model("catalogue.ReadingPath").objects.filter(
            is_published=True, editorial_status=""
        ),
        fields=("title", "description", "level"),
        refs={"category": "domaine"},
        fixed=EDITORIAL,
        children=(
            Child(
                "etapes",
                "catalogue.ReadingPathStep",
                "path",
                ("guide",),
                ("position", "note"),
                refs={"guide": "guide"},
            ),
        ),
    ),
    Spec(
        "cours",
        "Cours",
        "courses.Course",
        "slug",
        lambda: _model("courses.Course").objects.filter(status="publie"),
        fields=(
            "kind",
            "title",
            "summary",
            "level",
            "plan",
            "brief",
            "llm_model",
            "generated_at",
            "published_at",
        ),
        refs={"category": "domaine", "guide": "guide"},
        fixed={**GENERATED, "status": "publie", "published_by": None},
        children=(
            Child(
                "lecons",
                "courses.Lesson",
                "course",
                ("position",),
                ("position", "title", "objective", "body", "key_points", "sources", "minutes"),
            ),
            Child(
                "questions",
                "courses.CourseQuestion",
                "course",
                ("lesson", "position"),
                ("position", "text", "choices", "answer", "explanation"),
                siblings={"lesson": "lecons"},
            ),
        ),
    ),
    Spec(
        "podcast",
        "Podcasts",
        "podcasts.Series",
        "slug",
        lambda: _model("podcasts.Series").objects.filter(status="publie"),
        fields=(
            "title",
            "summary",
            "level",
            "plan",
            "brief",
            "llm_model",
            "voice",
            "duration_seconds",
            "generated_at",
            "approved_at",
            "published_at",
        ),
        refs={"category": "domaine"},
        fixed={**GENERATED, "status": "publie", "approved_by": None},
        children=(
            Child(
                "episodes",
                "podcasts.Episode",
                "series",
                ("position",),
                ("position", "title", "summary", "script", "sources", "flags", "duration_seconds"),
                files=("audio",),
                fixed={"needs_recording": False},
            ),
        ),
    ),
)
BY_KIND = {spec.kind: spec for spec in SPECS}


# --- Sérialisation
# -------------------------------------------------------------------------------------------
def _plain(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _key(spec, obj):
    if spec.key == "slug":
        return obj.slug
    return getattr(obj, spec.key).slug


def _child_key(child, obj, children):
    values = []
    for name in child.key:
        if name in child.siblings:
            target = getattr(obj, name)
            values.append(
                _child_key(children[child.siblings[name]], target, children) if target else None
            )
        elif name in child.refs:
            target = getattr(obj, name)
            values.append(target.slug if target else None)
        else:
            values.append(_plain(getattr(obj, name)))
    return json.dumps(values, ensure_ascii=False)


def _ref(target):
    return target.slug if target is not None else None


def serialize(spec, obj, file_ref):
    """Contenu sous forme JSON ; file_ref(fichier) donne la référence d'un fichier (empreinte, ou
    nom local)."""
    item = {"key": _key(spec, obj)}
    for name in spec.fields:
        item[name] = _plain(getattr(obj, name))
    for name in spec.files:
        item[name] = file_ref(getattr(obj, name))
    for name in spec.refs:
        item[name] = _ref(getattr(obj, name))
    for name in spec.m2m:
        item[name] = sorted(getattr(obj, name).values_list("slug", flat=True))
    children = {c.name: c for c in spec.children}
    for child in spec.children:
        rows = []
        for row in _model(child.model).objects.filter(**{child.parent: obj}):
            data = {"_key": _child_key(child, row, children)}
            data.update({name: _plain(getattr(row, name)) for name in child.fields})
            data.update({name: file_ref(getattr(row, name)) for name in child.files})
            data.update({name: _ref(getattr(row, name)) for name in child.refs})
            for name, sibling in child.siblings.items():
                target = getattr(row, name)
                data[name] = _child_key(children[sibling], target, children) if target else None
            rows.append(data)
        item[child.name] = sorted(rows, key=lambda r: r["_key"])
    return item


def digest(item):
    return hashlib.sha256(
        json.dumps(item, sort_keys=True, ensure_ascii=False, cls=DjangoJSONEncoder).encode()
    ).hexdigest()


def local_digest(spec, obj):
    return digest(serialize(spec, obj, lambda f: f.name if f else None))


# --- Export
# --------------------------------------------------------------------------------------------------
def export_pack(stream, version, log=lambda message: None):
    """Écrit le pack dans `stream` (fichier ou sortie standard), en flux."""
    written, data, counts, total = set(), {}, {}, 0

    with tarfile.open(fileobj=stream, mode="w|") as tar:
        def add(name, payload):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(payload), int(timezone.now().timestamp()), 0o644
            tar.addfile(info, _Reader(payload))

        def file_ref(field_file):
            nonlocal total
            if not field_file:
                return None
            try:
                with field_file.open("rb") as handle:
                    payload = handle.read()
            except OSError:  # dont FileNotFoundError
                log(f"fichier absent, ignoré : {field_file.name}")
                return None
            sha = hashlib.sha256(payload).hexdigest()
            ext = posixpath.splitext(field_file.name)[1].lower()[:10]
            if sha not in written:
                add(f"media/{sha}{ext}", payload)
                written.add(sha)
                total += len(payload)
            return {"sha": sha, "ext": ext}

        for spec in SPECS:
            items = [serialize(spec, obj, file_ref) for obj in spec.roots()]
            data[spec.kind], counts[spec.kind] = items, len(items)
            log(f"{spec.label} : {len(items)}")
        payload = json.dumps(data, ensure_ascii=False, cls=DjangoJSONEncoder).encode()
        add("data.json", payload)
        recorder = MigrationRecorder.Migration.objects
        manifest = {
            "format": FORMAT,
            "version": version,
            "built_at": timezone.now().isoformat(),
            "origin": settings.INSTANCE_NAME,
            "data_sha256": hashlib.sha256(payload).hexdigest(),
            "migrations": sorted(
                f"{a}.{n}"
                for a, n in recorder.filter(app__in=CONTENT_APPS).values_list("app", "name")
            ),
            "counts": counts,
            "files": len(written),
            "bytes": total,
        }
        add("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    return manifest


class _Reader:
    """Fichier en mémoire pour tarfile, sans copie supplémentaire."""
    def __init__(self, payload):
        self.payload, self.offset = memoryview(payload), 0

    def read(self, size=-1):
        end = len(self.payload) if size < 0 else self.offset + size
        chunk = self.payload[self.offset:end]
        self.offset += len(chunk)
        return bytes(chunk)


# --- Import
# --------------------------------------------------------------------------------------------------
def _storage_name(ref):
    return f"{MEDIA_DIR}/{ref['sha'][:2]}/{ref['sha']}{ref['ext']}"


def read_pack(stream, save_media=True):
    """Lit l'archive en flux : fichiers enregistrés (vérifiés par leur empreinte), puis données et
    manifeste."""
    data_bytes = manifest = None
    media = 0
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            if not member.isfile():
                raise PackError(f"Élément inattendu dans le pack : {member.name}")
            if member.size > MAX_MEMBER_BYTES:
                raise PackError(f"Fichier trop gros dans le pack : {member.name}")
            payload = tar.extractfile(member).read()
            if member.name.startswith("media/"):
                base = posixpath.basename(member.name)
                sha, ext = base[:64], base[64:]
                if hashlib.sha256(payload).hexdigest() != sha:
                    raise PackError(f"Fichier altéré dans le pack : {member.name}")
                name = _storage_name({"sha": sha, "ext": ext})
                if save_media and not default_storage.exists(name):
                    default_storage.save(name, ContentFile(payload))
                media += 1
            elif member.name == "data.json":
                data_bytes = payload
            elif member.name == "manifest.json":
                manifest = json.loads(payload)
            else:
                raise PackError(f"Élément inattendu dans le pack : {member.name}")
    if manifest is None or data_bytes is None:
        raise PackError("Pack incomplet (manifeste ou données absents).")
    if manifest.get("format") != FORMAT:
        raise PackError(
            f"Format de pack {manifest.get('format')} non pris en charge (attendu : {FORMAT})."
        )
    if hashlib.sha256(data_bytes).hexdigest() != manifest.get("data_sha256"):
        raise PackError("Données du pack altérées (empreinte différente du manifeste).")
    applied = {
        f"{a}.{n}" for a, n in MigrationRecorder.Migration.objects.values_list("app", "name")
    }
    missing = [m for m in manifest.get("migrations", []) if m not in applied]
    if missing:
        raise PackError(
            "Ce pack vient d'une version plus récente de l'application : mettez d'abord le code à"
            f" jour (./scripts/deploy.sh). Migrations manquantes : {', '.join(missing[:5])}…"
        )
    return manifest, json.loads(data_bytes), media


class Importer:
    def __init__(self, manifest, force=False):
        self.version, self.force = manifest["version"], force
        self.report = {
            kind: {"crees": 0, "mis_a_jour": 0, "inchanges": 0, "conserves": [], "erreurs": []}
            for kind in BY_KIND
        }
        self.cache = {}

    def resolve(self, kind, key):
        if key is None:
            return None
        if (kind, key) not in self.cache:
            self.cache[(kind, key)] = _model(BY_KIND[kind].model).objects.filter(slug=key).first()
        return self.cache[(kind, key)]

    def run(self, data):
        for spec in SPECS:
            for item in data.get(spec.kind, []):
                try:
                    with transaction.atomic():
                        self.apply(spec, item)
                except (IntegrityError, ValueError, KeyError) as exc:
                    self.report[spec.kind]["erreurs"].append(f"{item.get('key')} : {exc}")
        return self.report

    def find(self, spec, key):
        model = _model(spec.model)
        lookup = {"slug": key} if spec.key == "slug" else {f"{spec.key}__slug": key}
        return model.objects.filter(**lookup).first()

    def apply(self, spec, item):
        Imported = _model("catalogue.ImportedContent")
        stats, key = self.report[spec.kind], item["key"]
        obj = self.find(spec, key)
        record = Imported.objects.filter(kind=spec.kind, key=key).first()
        pack_digest = digest(item)
        if obj is None and spec.attach:
            stats["conserves"].append(f"{key} (guide absent de cette instance)")
            return
        if obj is not None and not self.force:
            current = local_digest(spec, obj)
            if record and record.pack_digest == pack_digest and record.local_digest == current:
                stats["inchanges"] += 1
                return
            if record and record.local_digest != current:
                stats["conserves"].append(f"{key} (modifié sur cette instance)")
                return
            hidden = [
                name
                for name in VISIBILITY
                if name in spec.fixed and getattr(obj, name) != spec.fixed[name]
            ]
            if (
                record and hidden
            ):  # dépublié ou repris en rédaction ici : on ne le remet pas en ligne
                stats["conserves"].append(
                    f"{key} (retiré ou repris en rédaction sur cette instance)"
                )
                return
            if not record and getattr(obj, "author_id", None):
                stats["conserves"].append(f"{key} (écrit par un rédacteur de cette instance)")
                return
        created = obj is None
        if created:
            obj = _model(spec.model)()
        if not spec.attach:
            if spec.key == "slug":
                obj.slug = key
            for name in spec.fields:
                setattr(obj, name, item.get(name))
            for name in spec.files:
                setattr(obj, name, _storage_name(item[name]) if item.get(name) else "")
            for name, kind in spec.refs.items():
                setattr(obj, name, self.resolve(kind, item.get(name)))
            for name, value in spec.fixed.items():
                setattr(obj, name, value)
            obj.save()
            self.cache[(spec.kind, key)] = obj
            for name, kind in spec.m2m.items():
                getattr(obj, name).set(
                    [t for t in (self.resolve(kind, k) for k in item.get(name, [])) if t]
                )
        self.sync_children(spec, obj, item)
        Imported.objects.update_or_create(
            kind=spec.kind,
            key=key,
            defaults={
                "pack_digest": pack_digest,
                "local_digest": local_digest(spec, obj),
                "pack_version": self.version,
            },
        )
        stats["crees" if created else "mis_a_jour"] += 1

    def sync_children(self, spec, obj, item):
        children = {c.name: c for c in spec.children}
        index = {}
        for child in spec.children:
            model = _model(child.model)
            existing = {
                _child_key(child, row, children): row
                for row in model.objects.filter(**{child.parent: obj})
            }
            index[child.name], seen = {}, set()
            for data in item.get(child.name, []):
                row = existing.get(data["_key"]) or model(**{child.parent: obj})
                for name in child.fields:
                    if (
                        name in data
                    ):  # champ absent d'un paquet plus ancien : valeur par défaut du modèle
                        setattr(row, name, data[name])
                for name in child.files:
                    setattr(row, name, _storage_name(data[name]) if data.get(name) else "")
                for name, kind in child.refs.items():
                    setattr(row, name, self.resolve(kind, data.get(name)))
                for name, value in child.fixed.items():
                    setattr(row, name, value)
                if not row.pk:  # liens entre éléments posés ensuite, une fois tous créés
                    for name in child.siblings:
                        setattr(row, name, None)
                row.save()
                index[child.name][data["_key"]] = row
                seen.add(data["_key"])
            index[child.name]["_extra"] = [row for k, row in existing.items() if k not in seen]
        for child in spec.children:
            for data in item.get(child.name, []):
                if not child.siblings:
                    break
                row = index[child.name][data["_key"]]
                for name, sibling in child.siblings.items():
                    setattr(
                        row, name, index[sibling].get(data.get(name)) if data.get(name) else None
                    )
                row.save(update_fields=list(child.siblings))
        for child in spec.children:
            for row in index[child.name]["_extra"]:
                row.delete()


def import_pack(stream, force=False, dry_run=False):
    """Importe un pack ; renvoie (manifeste, bilan). En essai, rien n'est enregistré."""
    manifest, data, media = read_pack(stream, save_media=not dry_run)
    with transaction.atomic():
        report = Importer(manifest, force=force).run(data)
        if dry_run:
            transaction.set_rollback(True)
        else:
            _model("catalogue.ContentImport").objects.create(
                version=manifest["version"],
                origin=manifest.get("origin", ""),
                built_at=manifest.get("built_at"),
                forced=force,
                report=report,
            )
    return manifest, report, media
