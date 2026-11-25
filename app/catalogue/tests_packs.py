"""Packs de contenus : export, installation, mise à jour sur place, modifications locales,
intégrité, vie privée."""

import io
import json
import shutil
import tarfile
import tempfile

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import User
from courses.models import Course, CourseQuestion, Lesson, LessonProgress
from podcasts.models import Episode, Series

from . import packs
from .models import (
    Certification,
    ContentImport,
    Glossary,
    GlossaryEntry,
    Guide,
    GuideCategory,
    GuideCitation,
    GuideDocument,
    ImportedContent,
    Narration,
    NarrationSection,
    QuizAttempt,
    QuizQuestion,
    ReadingListItem,
    Referential,
    Requirement,
)

CONTENT_MODELS = (
    GuideCategory,
    Guide,
    Referential,
    Certification,
    Glossary,
    Narration,
    Course,
    Series,
)


class PackTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media, True)
        self.vendor = User.objects.create_user("editeur", email="editeur@example.org")
        self.build_catalogue()

    def build_catalogue(self):
        domain = GuideCategory.objects.create(name="Réseau", slug="reseau")
        guide = Guide.objects.create(
            title="Pare-feu",
            slug="pare-feu",
            anssi_id="pare-feu",
            category=domain,
            author=self.vendor,
        )
        document = GuideDocument(guide=guide, label="Guide", position=1)
        document.file.save("guide.pdf", ContentFile(b"%PDF-1.7 contenu"), save=True)
        for n in (1, 2):
            QuizQuestion.objects.create(guide=guide, series=1, position=n, text=f"Question {n} ?",
                                        choices=["A", "B", "C"], answer=0)
        glossary = Glossary.objects.create(guide=guide, status="publie", requested_by=self.vendor)
        GlossaryEntry.objects.create(
            glossary=glossary, term="SI", key="si", kind="sigle", definition="Système d'information"
        )
        narration = Narration.objects.create(guide=guide, status="prete", voice="developpeuse")
        section = NarrationSection(narration=narration, position=1, title="Introduction", text="…")
        section.audio.save("s1.mp3", ContentFile(b"ID3 audio"), save=True)
        referential = Referential.objects.create(name="Hygiène", slug="hygiene", issuer="ANSSI")
        parent = Requirement.objects.create(
            referential=referential, kind="section", ref="1", text="Sensibiliser", position=1
        )
        Requirement.objects.create(
            referential=referential,
            parent=parent,
            kind="requirement",
            ref="1.1",
            text="Former les équipes",
            position=2,
        )
        GuideCitation.objects.create(
            referential=referential, code="R1", title="Pare-feu", guide=guide, position=1
        )
        certification = Certification.objects.create(
            name="Visa", slug="visa", kind="product", issuer="ANSSI"
        )
        certification.referentials.add(referential)
        course = Course.objects.create(
            kind="guide",
            guide=guide,
            category=domain,
            title="Bases du pare-feu",
            slug="bases-pare-feu",
            status="publie",
            published_by=self.vendor,
        )
        lesson = Lesson.objects.create(
            course=course, position=1, title="Rôle", body="Un pare-feu filtre."
        )
        CourseQuestion.objects.create(
            course=course,
            lesson=lesson,
            position=1,
            text="Filtre ?",
            choices=["Oui", "Non"],
            answer=0,
        )
        CourseQuestion.objects.create(
            course=course,
            lesson=None,
            position=1,
            text="Examen ?",
            choices=["Oui", "Non"],
            answer=0,
        )
        series = Series.objects.create(
            category=domain, title="Réseau en clair", slug="reseau-en-clair", status="publie"
        )
        episode = Episode(
            series=series,
            position=1,
            title="Épisode 1",
            script=[{"speaker": "Clara", "text": "Bonjour"}],
        )
        episode.audio.save("e1.mp3", ContentFile(b"ID3 podcast"), save=True)

    def export(self, version="1"):
        stream = io.BytesIO()
        packs.export_pack(stream, version)
        stream.seek(0)
        return stream

    def wipe(self):
        """Instance vierge : contenus supprimés (les comptes restent)."""
        for model in CONTENT_MODELS:
            model.objects.all().delete()
        ImportedContent.objects.all().delete()

    def test_export_contains_content_but_no_personal_data(self):
        learner = User.objects.create_user("apprenant", email="perso@example.org")
        ReadingListItem.objects.create(user=learner, guide=Guide.objects.get())
        raw = self.export().getvalue()
        names = tarfile.open(fileobj=io.BytesIO(raw)).getnames()
        self.assertEqual(names[-2:], ["data.json", "manifest.json"])
        self.assertEqual(sum(n.startswith("media/") for n in names), 3)
        for secret in (b"apprenant", b"perso@example.org", b"editeur"):
            self.assertNotIn(secret, raw)

    def test_install_on_blank_instance(self):
        pack = self.export()
        self.wipe()
        _manifest, report, media = packs.import_pack(pack)
        self.assertEqual(media, 3)
        guide = Guide.objects.get(slug="pare-feu")
        self.assertIsNone(guide.author)
        self.assertEqual(guide.category.slug, "reseau")
        self.assertEqual(guide.documents.get().file.read(), b"%PDF-1.7 contenu")
        self.assertEqual(QuizQuestion.objects.filter(guide=guide).count(), 2)
        self.assertEqual(GlossaryEntry.objects.get().definition, "Système d'information")
        self.assertEqual(NarrationSection.objects.get().audio.read(), b"ID3 audio")
        child = Requirement.objects.get(ref="1.1")
        self.assertEqual(child.parent.ref, "1")
        self.assertEqual(GuideCitation.objects.get().guide, guide)
        self.assertEqual(
            list(Certification.objects.get().referentials.values_list("slug", flat=True)),
            ["hygiene"],
        )
        course = Course.objects.get()
        self.assertEqual((course.status, course.published_by), ("publie", None))
        self.assertEqual(CourseQuestion.objects.get(lesson__isnull=False).lesson.title, "Rôle")
        self.assertEqual(Episode.objects.get().audio.read(), b"ID3 podcast")
        self.assertEqual(report["cours"]["crees"], 1)
        self.assertEqual(ContentImport.objects.get().version, "1")

    def test_update_in_place_keeps_progress(self):
        pack = self.export()
        self.wipe()
        packs.import_pack(pack)
        learner = User.objects.create_user("apprenant")
        lesson = Lesson.objects.get()
        question = QuizQuestion.objects.get(position=1)
        LessonProgress.objects.create(user=learner, lesson=lesson, best_score=100)
        QuizAttempt.objects.create(user=learner, guide=question.guide, series=1, score=1, total=2,
                                   answers={str(question.pk): 0})
        # Nouvelle version chez l'éditeur : leçon corrigée, question reformulée, une question en
        # plus.
        Lesson.objects.filter(pk=lesson.pk).update(body="Un pare-feu filtre les flux réseau.")
        QuizQuestion.objects.filter(pk=question.pk).update(text="Question 1 reformulée ?")
        QuizQuestion.objects.create(
            guide=question.guide,
            series=1,
            position=3,
            text="Question 3 ?",
            choices=["A", "B"],
            answer=0,
        )
        pack2 = self.export("2")
        Lesson.objects.filter(pk=lesson.pk).update(
            body="Un pare-feu filtre."
        )  # instance cliente : ancienne version
        QuizQuestion.objects.filter(pk=question.pk).update(text="Question 1 ?")
        QuizQuestion.objects.filter(position=3).delete()
        ImportedContent.objects.filter(kind__in=("cours", "qcm")).update(
            local_digest=""
        )  # empreintes de l'état « v1 »
        for kind, spec in (("cours", packs.BY_KIND["cours"]), ("qcm", packs.BY_KIND["qcm"])):
            obj = Course.objects.get() if kind == "cours" else Guide.objects.get()
            ImportedContent.objects.filter(kind=kind).update(
                local_digest=packs.local_digest(spec, obj)
            )
        packs.import_pack(pack2)
        lesson.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual(lesson.body, "Un pare-feu filtre les flux réseau.")
        self.assertEqual(question.text, "Question 1 reformulée ?")
        self.assertEqual(QuizQuestion.objects.count(), 3)
        self.assertTrue(LessonProgress.objects.filter(lesson=lesson, user=learner).exists())
        self.assertEqual(QuizAttempt.objects.get().answers, {str(question.pk): 0})

    def test_local_changes_are_kept_unless_forced(self):
        pack = self.export()
        self.wipe()
        packs.import_pack(pack)
        GlossaryEntry.objects.update(definition="Définition adaptée par le client")
        _, report, _ = packs.import_pack(self.export_with_vendor_change())
        self.assertEqual(GlossaryEntry.objects.get().definition, "Définition adaptée par le client")
        self.assertIn("modifié sur cette instance", report["lexique"]["conserves"][0])
        packs.import_pack(self.export_with_vendor_change(), force=True)
        self.assertEqual(GlossaryEntry.objects.get().definition, "Système d'information (SI)")

    def export_with_vendor_change(self):
        current = GlossaryEntry.objects.get().definition
        GlossaryEntry.objects.update(definition="Système d'information (SI)")
        pack = self.export("2")
        GlossaryEntry.objects.update(definition=current)
        return pack

    def test_content_written_on_instance_is_never_overwritten(self):
        pack = self.export()
        self.wipe()
        client_author = User.objects.create_user("redactrice")
        Course.objects.create(
            kind="theme",
            title="Cours maison",
            slug="bases-pare-feu",
            status="publie",
            author=client_author,
        )
        _, report, _ = packs.import_pack(pack)
        self.assertEqual(Course.objects.get(slug="bases-pare-feu").title, "Cours maison")
        self.assertIn("rédacteur de cette instance", report["cours"]["conserves"][0])

    def test_unchanged_pack_is_noop_and_dry_run_saves_nothing(self):
        pack = self.export()
        self.wipe()
        _, report, _ = packs.import_pack(pack, dry_run=True)
        self.assertEqual(report["guide"]["crees"], 1)
        self.assertFalse(Guide.objects.exists())
        pack.seek(0)
        packs.import_pack(pack)
        pack.seek(0)
        _, report, _ = packs.import_pack(pack)
        self.assertEqual(report["guide"]["inchanges"], 1)
        self.assertEqual(report["cours"]["mis_a_jour"], 0)

    def test_tampered_pack_is_refused(self):
        raw = bytearray(self.export().getvalue())
        position = raw.find(b"%PDF-1.7 contenu")
        raw[position:position + 4] = b"%EVL"
        with self.assertRaisesMessage(packs.PackError, "altéré"):
            packs.import_pack(io.BytesIO(bytes(raw)))
        data = self.export().getvalue().replace(b"Pare-feu", b"Pare-fou")
        with self.assertRaisesMessage(packs.PackError, "altérées"):
            packs.import_pack(io.BytesIO(data))

    def test_pack_from_newer_application_is_refused(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for name, payload in (
                ("data.json", b"{}"),
                (
                    "manifest.json",
                    json.dumps(
                        {
                            "format": 1,
                            "version": "9",
                            "migrations": ["catalogue.9999_futur"],
                            "data_sha256": __import__("hashlib").sha256(b"{}").hexdigest(),
                        }
                    ).encode(),
                ),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        stream.seek(0)
        with self.assertRaisesMessage(packs.PackError, "version plus récente"):
            packs.import_pack(stream)

    def test_commands(self):
        out = io.StringIO()
        call_command("importer_contenus", "--version-installee", stdout=out)
        self.assertEqual(out.getvalue().strip(), "")
        path = f"{self.media}/pack.tar"
        call_command("exporter_contenus", path, "--numero", "2026.11")
        self.wipe()
        out = io.StringIO()
        call_command("importer_contenus", path, stdout=out)
        self.assertIn("Import terminé", out.getvalue())
        out = io.StringIO()
        call_command("importer_contenus", "--version-installee", stdout=out)
        self.assertEqual(out.getvalue().strip(), "2026.11")

    def test_content_hidden_on_instance_stays_hidden(self):
        pack = self.export()
        self.wipe()
        packs.import_pack(pack)
        Guide.objects.update(summary="Résumé revu par l'éditeur")  # version 2, différente
        pack2 = self.export("2")
        Guide.objects.update(summary="")
        ImportedContent.objects.filter(kind="guide").update(
            local_digest=packs.local_digest(packs.BY_KIND["guide"], Guide.objects.get()))
        Guide.objects.update(is_published=False)  # le client masque le guide
        _, report, _ = packs.import_pack(pack2)
        self.assertFalse(Guide.objects.get().is_published)
        self.assertIn("retiré", report["guide"]["conserves"][0])
