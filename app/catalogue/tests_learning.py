"""Tests du planning, de l'import des contenus ANSSI et des grilles d'exigences."""
import io
from datetime import date, timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from progression.models import XPEvent

from .importers.citations import best_guide, parse_citations
from .importers.grids import sync_requirements
from .importers.guides import reading_minutes
from .importers.textgrid import parse_lettered, parse_recyf
from .importers.trames import Row, parse_trame
from .models import (
    Certification,
    Guide,
    GuideCategory,
    GuideCitation,
    GuideDocument,
    Level,
    ReadingListItem,
    QuizAttempt,
    QuizQuestion,
    Referential,
    Requirement,
    RequirementProgress,
    StudyPlan,
)
from . import reading
from .planning import build_schedule

User = get_user_model()
STRONG_PASSWORD = "Un-mot-de-passe-solide-42!"


def _guide(title, minutes):
    return SimpleNamespace(title=title, estimated_minutes=minutes)


class PlanningTests(TestCase):
    def test_items_are_packed_into_daily_budget(self):
        days = build_schedule([_guide("a", 10), _guide("b", 20), _guide("c", 15)], 30,
                              today=date(2026, 10, 14))
        self.assertEqual([[g.title for g in d.items] for d in days], [["a", "b"], ["c"]])
        self.assertEqual([d.date for d in days], [date(2026, 10, 14), date(2026, 10, 15)])
        self.assertEqual(days[0].week_start, date(2026, 10, 12))

    def test_long_document_spans_several_days(self):
        days = build_schedule(
            [_guide("long", 100), _guide("court", 20)], 40, today=date(2026, 10, 12)
        )
        self.assertEqual(len(days), 4)
        self.assertEqual(days[0].items[0].title, "long")
        self.assertEqual(days[1].continued.title, "long")
        self.assertEqual(days[2].continued.title, "long")
        self.assertEqual(days[3].items[0].title, "court")

    def test_empty_queue(self):
        self.assertEqual(build_schedule([], 60), [])

    def test_only_chosen_weekdays_get_readings(self):
        # Vendredi 16/10/2026, lecture les lundis et mercredis seulement.
        days = build_schedule([_guide("a", 30), _guide("long", 70), _guide("c", 10)], 30,
                              today=date(2026, 10, 16), weekdays=[0, 2])
        self.assertEqual(
            [d.date for d in days],
            [
                date(2026, 10, 19),
                date(2026, 10, 21),
                date(2026, 10, 26),
                date(2026, 10, 28),
                date(2026, 11, 2),
            ],
        )
        self.assertEqual(days[2].continued.title, "long")  # suite du guide long le lundi suivant
        self.assertEqual(days[4].items[0].title, "c")


class ReadingTimeTests(TestCase):
    def test_duration(self):
        self.assertEqual(reading_minutes(40), 100)
        self.assertEqual(reading_minutes(0), 30)


class GridParserTests(TestCase):
    def _trame(self):
        from openpyxl import Workbook
        workbook = Workbook()
        annex = workbook.active
        annex.title = "2 - II901"
        annex.append(["Thème", "IR Ref", "Type", "Exigence(s) détaillée(s)"])
        annex.append(["Annexe", "X.1", "Exigence", "Ne doit pas être importée"])
        annex.append(["Annexe", "X.2", "Exigence", "Ne doit pas être importée"])
        annex.append(["Annexe", "X.3", "Exigence", "Ne doit pas être importée"])
        sheet = workbook.create_sheet("1 - Exigences")
        sheet.append(["MENTION DE PROTECTION"])
        sheet.append(["Thème", "IR Ref", "Type", "Exigence(s) détaillée(s)",
                      "Vérifications à réaliser par le centre", "Précision(s)",
                      "Type(s) / exemple(s) de preuve"])
        sheet.append(
            ["Généralités", "IV.1.a", "Exigence", "Le prestataire doit être une personne morale."]
        )
        sheet.append(
            [
                "Généralités",
                "IV.1.a_V01",
                "Vérification",
                None,
                "Vérifier le K-Bis.",
                "Précision",
                "K-Bis",
            ]
        )
        sheet.append(["Généralités", "IV.1.b", "Recommandation", "Il est recommandé de…"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def test_trame_requirements_and_checks(self):
        rows = parse_trame(self._trame())
        self.assertEqual([r.ref for r in rows], ["IV.1.a", "IV.1.b"])
        self.assertEqual(rows[0].checks[0].text, "Vérifier le K-Bis.")
        self.assertEqual(rows[0].checks[0].evidence, "K-Bis")
        self.assertEqual(rows[1].kind, "recommendation")

    def test_lettered_pdf_text(self):
        lines = [
            "IV.2.1. Appréciation des risques",
            "a) Le prestataire doit réaliser une",
            "appréciation des risques.",
            "b) Il est recommandé que le prestataire la révise.",
            "c) Texte descriptif sans obligation.",
        ]
        rows = parse_lettered(lines)
        self.assertEqual([r.ref for r in rows], ["IV.2.1.a)", "IV.2.1.b)"])
        self.assertEqual(rows[0].text, "Le prestataire doit réaliser une appréciation des risques.")
        self.assertEqual(rows[1].kind, "recommendation")

    def test_recyf_text(self):
        lines = ["Objectif de sécurité 1. Recensement des SI ........ 7",
                 "OBJECTIF DE SÉCURITÉ 1.  RECENSEMENT",
                 "1.1-EI/EE", "L'entité liste ses activités.", "Oui Oui",
                 "7.A.5-EE", "Passerelle sortante.", "Non Oui"]
        rows = parse_recyf(lines)
        self.assertEqual([r.ref for r in rows], ["1.1-EI/EE", "7.A.5-EE"])
        self.assertEqual(rows[0].theme, "Objectif 1. Recensement des SI")
        self.assertIn("entité importante : Non", rows[1].guidance)


class GridSyncTests(TestCase):
    def test_resync_keeps_user_progress(self):
        user = User.objects.create_user("alice", password=STRONG_PASSWORD)
        referential = Referential.objects.create(name="Test", slug="test")
        first = Row(
            "requirement", "A", "T", "Texte A", checks=[Row("verification", "A_V1", "T", "Vérif")]
        )
        sync_requirements(referential, [first, Row("requirement", "B", "T", "Texte B")])
        requirement = referential.requirements.get(ref="A", kind="requirement")
        RequirementProgress.objects.create(user=user, requirement=requirement, status="done")

        sync_requirements(referential, [Row("requirement", "A", "T", "Texte A modifié")])
        requirement.refresh_from_db()
        self.assertEqual(requirement.text, "Texte A modifié")
        self.assertEqual(RequirementProgress.objects.get(user=user).status, "done")
        self.assertFalse(referential.requirements.filter(ref="B").exists())
        self.assertFalse(referential.requirements.filter(kind="verification").exists())


class ReadingListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password=STRONG_PASSWORD)
        self.client.force_login(self.user)
        self.guide = Guide.objects.create(title="Sécurité de l'Active Directory", slug="ad",
                                          estimated_minutes=20, summary="Durcir un annuaire.")
        self.long = Guide.objects.create(title="Guide long", slug="long", estimated_minutes=200,
                                         level=Level.ADVANCED)
        self.document = GuideDocument.objects.create(
            guide=self.guide, label="Le guide", file=ContentFile(b"%PDF-1.7 test", name="ad.pdf"))
        self.referential = Referential.objects.create(
            name="Prestataires d'audit", short_name="PASSI", slug="passi", estimated_minutes=90,
            document=ContentFile(b"%PDF-1.7 ref", name="passi.pdf"))

    def tearDown(self):
        self.document.file.delete(save=False)
        self.referential.document.delete(save=False)

    def _add(self, kind, slug):
        return self.client.post(
            reverse("catalogue:reading_add"), {"kind": kind, "slug": slug, "next": "/recherche/"}
        )

    def test_search_is_accent_insensitive_and_offers_plus(self):
        response = self.client.get(reverse("catalogue:search"), {"q": "securite active"})
        self.assertEqual(response.context["results"]["guides"], [self.guide])
        self.assertContains(response, 'class="add"')
        response = self.client.get(reverse("catalogue:search"), {"q": "passi"})
        self.assertEqual(response.context["results"]["referentiels"], [self.referential])

    def test_add_guide_and_referential_in_order(self):
        self.assertRedirects(
            self._add("guide", "long"), "/recherche/", fetch_redirect_response=False
        )
        self._add("referentiel", "passi")
        self._add("guide", "long")  # déjà présent : pas de doublon
        items = list(ReadingListItem.objects.filter(user=self.user))
        self.assertEqual([i.document for i in items], [self.long, self.referential])

    def test_external_next_is_ignored(self):
        response = self.client.post(
            reverse("catalogue:reading_add"),
            {"kind": "guide", "slug": "ad", "next": "https://evil.example/"},
        )
        self.assertRedirects(response, reverse("catalogue:reading_list"))

    def test_reorder_finish_and_remove(self):
        first, _ = reading.add(self.user, self.guide)
        second, _ = reading.add(self.user, self.long)
        url = reverse("catalogue:reading_item", args=[second.pk])
        self.client.post(url, {"action": "monter"})
        self.assertEqual([i.document for i in reading.queue(self.user)], [self.long, self.guide])
        self.client.post(url, {"action": "lu"})
        self.assertEqual([i.document for i in reading.queue(self.user)], [self.guide])
        self.client.post(reverse("catalogue:reading_item", args=[first.pk]), {"action": "retirer"})
        self.assertFalse(ReadingListItem.objects.filter(pk=first.pk).exists())

    def test_items_of_other_users_are_untouchable(self):
        other = User.objects.create_user("bob", password=STRONG_PASSWORD)
        item, _ = reading.add(other, self.guide)
        response = self.client.post(
            reverse("catalogue:reading_item", args=[item.pk]), {"action": "retirer"}
        )
        self.assertEqual(response.status_code, 404)

    def test_opening_a_document_marks_it_started(self):
        reading.add(self.user, self.guide)
        self.client.get(reverse("catalogue:guide_read", args=["ad", self.document.pk]))
        self.assertIsNotNone(reading.get_item(self.user, self.guide).started_at)
        response = self.client.get(reverse("catalogue:referential_read", args=["passi"]))
        self.assertContains(response, "<iframe")

    def test_detail_is_an_information_sheet_before_reading(self):
        response = self.client.get(reverse("catalogue:guide_detail", args=["ad"]))
        self.assertContains(
            response, reverse("catalogue:guide_read", args=["ad", self.document.pk])
        )
        self.assertNotContains(response, "<iframe")

    def test_document_requires_login(self):
        url = reverse("catalogue:guide_document", args=["ad", self.document.pk])
        response = self.client.get(url)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.7 test")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_study_plan_follows_reading_list(self):
        reading.add(self.user, self.guide)
        reading.add(self.user, self.referential)
        reading.add(self.user, self.long)
        response = self.client.post(reverse("catalogue:study_plan"),
                                    {"minutes_per_day": 120, "weekdays": [0, 1, 2, 3, 4, 5, 6]})
        self.assertRedirects(response, reverse("catalogue:study_plan"))
        days = self.client.get(reverse("catalogue:study_plan")).context["days"]
        self.assertEqual([[i.document for i in d.items] for d in days[:2]],
                         [[self.guide, self.referential], [self.long]])
        self.assertEqual(
            len(days), 3
        )  # 200 min à raison de 2 h par jour : 2 jours pour le guide long

        reading.mark_finished(self.user, self.guide)
        days = self.client.get(reverse("catalogue:study_plan")).context["days"]
        self.assertEqual(days[0].items[0].document, self.referential)

    def test_study_plan_weekdays(self):
        reading.add(self.user, self.guide)
        refused = self.client.post(reverse("catalogue:study_plan"), {"minutes_per_day": 60})
        self.assertContains(refused, "Choisissez au moins un jour.")
        form = self.client.get(reverse("catalogue:study_plan")).context["form"]
        self.assertEqual(
            form.initial["weekdays"], [0, 1, 2, 3, 4]
        )  # nouveau planning : jours ouvrés proposés
        today = timezone.localdate()
        other_days = [d for d in range(7) if d != today.weekday()]
        self.client.post(
            reverse("catalogue:study_plan"), {"minutes_per_day": 60, "weekdays": other_days}
        )
        plan = StudyPlan.objects.get(user=self.user)
        self.assertEqual(plan.weekdays, other_days)
        days = self.client.get(reverse("catalogue:study_plan")).context["days"]
        self.assertEqual(
            days[0].date, today + timedelta(days=1)
        )  # aujourd'hui n'est pas un jour de lecture
        dashboard = self.client.get(reverse("catalogue:dashboard"))
        self.assertEqual(dashboard.context["today_reading"], [])
        self.assertEqual(dashboard.context["next_reading_day"], today + timedelta(days=1))
        self.assertContains(dashboard, "Pas de lecture prévue aujourd'hui")

    def test_requirement_status(self):
        requirement = Requirement.objects.create(
            referential=self.referential, ref="IV.1.a", text="Exigence"
        )
        check = Requirement.objects.create(referential=self.referential, parent=requirement,
                                           kind="verification", ref="IV.1.a_V01", text="Vérif")
        url = reverse("catalogue:requirement_status", args=[requirement.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {"status": "done"})
        self.assertEqual(RequirementProgress.objects.get(user=self.user).status, "done")
        self.assertEqual(self.client.post(url, {"status": "piraté"}).status_code, 404)
        check_url = reverse("catalogue:requirement_status", args=[check.pk])
        self.assertEqual(self.client.post(check_url, {"status": "done"}).status_code, 404)
        response = self.client.get(reverse("catalogue:referential_detail", args=["passi"]))
        self.assertEqual(response.context["progress"], 100)

    def test_referential_offers_quizzes_of_cited_guides(self):
        url = reverse("catalogue:referential_detail", args=["passi"])
        GuideCitation.objects.create(
            referential=self.referential, title="Guide AD", guide=self.guide
        )
        GuideCitation.objects.create(
            referential=self.referential, title="Guide long", guide=self.long
        )
        self.assertNotContains(self.client.get(url), "Se tester")  # aucun guide cité n'a de QCM
        QuizQuestion.objects.create(
            guide=self.guide, series=1, text="Q ?", choices=["A", "B"], answer=0
        )
        QuizQuestion.objects.create(
            guide=self.guide, series=2, text="Q2 ?", choices=["A", "B"], answer=0
        )
        response = self.client.get(url)
        self.assertEqual(list(response.context["quiz_guides"]), [self.guide])
        self.assertContains(response, "2 séries")

    def test_referential_quiz_opens_once_read_and_rewards_apart_from_guides(self):
        questions = [
            QuizQuestion.objects.create(
                referential=self.referential,
                series=n,
                text=f"Exigence {n} ?",
                choices=["Bonne", "Mauvaise"],
                answer=0,
            )
            for n in (1, 2)
        ]
        url = reverse("catalogue:referential_quiz_take", args=["passi", 1])
        self.assertRedirects(
            self.client.get(url), reverse("catalogue:referential_detail", args=["passi"])
        )
        reading.mark_finished(self.user, self.referential)
        detail = self.client.get(reverse("catalogue:referential_detail", args=["passi"]))
        self.assertContains(detail, url)  # série 1 ouverte depuis la fiche
        response = self.client.post(url, {f"q{questions[0].pk}": "0"})
        self.assertEqual(response.context["attempt"].referential, self.referential)
        self.assertContains(response, "Revoir le référentiel")
        self.assertFalse(QuizAttempt.objects.filter(guide__isnull=False).exists())
        # Références de récompense distinctes : le guide de même numéro garde ses propres XP à
        # gagner
        self.assertTrue(
            XPEvent.objects.filter(user=self.user, ref=f"serie:ref{self.referential.pk}:1").exists()
        )
        self.assertFalse(
            XPEvent.objects.filter(user=self.user, ref=f"serie:{self.referential.pk}:1").exists()
        )


class CitationTests(TestCase):
    def test_both_reference_formats_are_parsed(self):
        lines = [
            "Le prestataire doit appliquer [HYGIENE] dans son SI.",
            "Annexe 1 Références documentaires",
            (
                "[HYGIENE] Guide d'Hygiène Informatique, ANSSI, version en vigueur. Disponible sur"
                " http://www.ssi.gouv.fr"
            ),
            "[NT_TLS]",
            "Recommandations de sécurité relatives à TLS, note technique",
            "n° SDE-NT-35, ANSSI.",
            "Disponible sur http://www.ssi.gouv.fr",
            "[RGPD] Règlement (UE) 2016/679. Disponible sur https://eur-lex.europa.eu",
            "12. Guide - Guide d'hygiène informatique, ANSSI, version en vigueur. Disponible sur",
            "1. La dégradation des activités de l'entité ;",
        ]
        citations = parse_citations(lines)
        by_code = {c.code: c for c in citations}
        self.assertEqual(by_code["HYGIENE"].title, "Guide d'Hygiène Informatique")
        self.assertEqual(by_code["NT_TLS"].title, "Recommandations de sécurité relatives à TLS")
        self.assertFalse(by_code["RGPD"].is_anssi)
        numbered = [c for c in citations if not c.code]
        self.assertEqual([c.title for c in numbered], ["Guide d'hygiène informatique"])

    def test_matching_titles_and_aliases(self):
        guides = [
            Guide(title="Guide d’hygiène informatique", language="fr"),
            Guide(title="L’homologation de sécurité des systèmes d’information", language="fr"),
            Guide(
                title="Recommandations relatives à l’interconnexion d’un SI à Internet",
                language="fr",
            ),
        ]
        self.assertEqual(best_guide("Guide d'Hygiène Informatique", guides), guides[0])
        self.assertEqual(
            best_guide("L’homologation de sécurité en neuf étapes simples", guides), guides[1]
        )
        self.assertIsNone(best_guide("Processus de qualification d’un service", guides))


class CrossLinkViewsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("alice", password=STRONG_PASSWORD)
        self.client.force_login(user)
        self.guide = Guide.objects.create(title="Guide d'hygiène informatique", slug="hygiene")
        self.referential = Referential.objects.create(
            name="SecNumCloud", short_name="SNC", slug="snc"
        )
        Requirement.objects.create(referential=self.referential, ref="05.01.a", text="Exigence")
        GuideCitation.objects.create(referential=self.referential, code="HYGIENE",
                                     title="Guide d'hygiène informatique", guide=self.guide)
        self.certification = Certification.objects.create(name="Qualification SecNumCloud",
                                                          acronym="SecNumCloud", slug="secnumcloud")
        self.certification.referentials.add(self.referential)

    def test_guide_page_lists_citing_certifications(self):
        response = self.client.get(reverse("catalogue:guide_detail", args=["hygiene"]))
        self.assertContains(response, self.certification.get_absolute_url())
        self.assertContains(response, "[HYGIENE]")

    def test_certification_page_lists_cited_guides(self):
        response = self.client.get(reverse("catalogue:certification_detail", args=["secnumcloud"]))
        self.assertEqual(response.context["cited_guides"], [self.guide])
        self.assertContains(response, reverse("catalogue:referential_export", args=["snc"]))
        self.client.post(reverse("catalogue:reading_add_certification", args=["secnumcloud"]))
        self.assertTrue(ReadingListItem.objects.filter(guide=self.guide).exists())

    def test_excel_exports(self):
        from openpyxl import load_workbook
        self.assertEqual(
            self.client.get(reverse("catalogue:referential_trame", args=["snc"])).status_code, 404
        )
        self.referential.trame.save("trame.xlsx", ContentFile(b"PK\x03\x04 trame"))
        response = self.client.get(reverse("catalogue:referential_trame", args=["snc"]))
        self.assertIn("attachment", response["Content-Disposition"])
        b"".join(response.streaming_content)
        self.referential.trame.delete(save=False)

        response = self.client.get(reverse("catalogue:referential_export", args=["snc"]))
        sheet = load_workbook(io.BytesIO(response.content)).active
        values = [cell.value for row in sheet.iter_rows() for cell in row]
        self.assertIn("05.01.a", values)
        self.assertIn("À faire", values)


class CatalogueFiltersTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("alice", password=STRONG_PASSWORD)
        self.client.force_login(user)
        self.cloud = GuideCategory.objects.create(name="Cloud", slug="cloud")
        self.network = GuideCategory.objects.create(name="Réseau", slug="reseau")
        guide = Guide.objects.create(
            title="Hébergement cloud", slug="cloud-guide", category=self.cloud
        )
        self.snc = Referential.objects.create(name="SecNumCloud", short_name="SNC", slug="snc")
        GuideCitation.objects.create(referential=self.snc, title="Hébergement cloud", guide=guide)
        self.exam = Referential.objects.create(
            name="Programme CISSP", slug="programme-cissp", kind="exam"
        )
        self.provider = Certification.objects.create(
            name="SecNumCloud", slug="qual-snc", kind="provider_qual", level=Level.ADVANCED
        )
        self.provider.referentials.add(self.snc)
        self.product = Certification.objects.create(name="Qualification produit", slug="qual-prod",
                                                    kind="product_qual")
        self.pro = Certification.objects.create(name="CISSP", slug="cissp", kind="individual",
                                                level=Level.ADVANCED)
        self.pro.referentials.add(self.exam)
        self.pro.guide_categories.add(self.network)

    def test_certifications_order_providers_then_products(self):
        response = self.client.get(reverse("catalogue:certification_list"))
        self.assertEqual(
            list(response.context["certifications"]), [self.provider, self.product, self.pro]
        )

    def test_exam_programmes_have_their_own_tab(self):
        referentials = self.client.get(reverse("catalogue:referential_list")).context["groups"]
        self.assertNotIn(self.exam, [r for group in referentials.values() for r in group])
        exams = self.client.get(reverse("catalogue:exam_list")).context["referentials"]
        self.assertEqual(list(exams), [self.exam])

    def test_same_filters_apply_to_every_tab(self):
        params = {"domaine": "cloud", "niveau": Level.ADVANCED}
        certifications = self.client.get(reverse("catalogue:certification_list"), params).context[
            "certifications"
        ]
        self.assertEqual(list(certifications), [self.provider])
        groups = self.client.get(reverse("catalogue:referential_list"), params).context["groups"]
        self.assertEqual([r for group in groups.values() for r in group], [self.snc])
        exams = self.client.get(reverse("catalogue:exam_list"), {"domaine": "reseau"}).context[
            "referentials"
        ]
        self.assertEqual(list(exams), [self.exam])
        response = self.client.get(
            reverse("catalogue:guide_list"), {"q": "a b&c", "domaine": "cloud"}
        )
        self.assertContains(response, "?q=a+b%26c&amp;domaine=cloud")
