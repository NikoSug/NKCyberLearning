"""Actualités cyber : lecture des flux, sécurité des téléchargements, tableau de bord."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import news
from .models import NewsItem

User = get_user_model()

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Flux</title>
  <item>
    <title>Vuln&#233;rabilit&#233; critique dans un pare-feu</title>
    <link>https://www.cert.ssi.gouv.fr/alerte/CERTFR-2026-ALE-001/</link>
    <guid>CERTFR-2026-ALE-001</guid>
    <pubDate>Mon, 12 Oct 2026 09:00:00 +0000</pubDate>
    <description><![CDATA[<p>Une <b>faille</b> permet une ex&eacute;cution de code.</p><script>alert(1)</script>]]></description>
  </item>
  <item>
    <title>Lien dangereux</title>
    <link>javascript:alert(1)</link>
    <pubDate>Mon, 12 Oct 2026 08:00:00 +0000</pubDate>
  </item>
</channel></rss>"""

LEAKS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title><![CDATA[🟢 École 42]]></title>
    <link><![CDATA[https://bonjourlafuite.eu.org/#%C3%89cole%2042-2026-10-10]]></link>
    <guid isPermaLink="false"><![CDATA[bonjour-la-fuite:École 42-2026-10-10]]></guid>
    <pubDate>Sat, 10 Oct 2026 00:00:00 +0000</pubDate>
    <description><![CDATA[<ul><li>Nom, prénom</li><li>Adresse email</li></ul>]]></description>
    <category>leak</category>
  </item>
  <item>
    <title><![CDATA[🟠 Clinique Exemple]]></title>
    <link><![CDATA[https://bonjourlafuite.eu.org/#Clinique-2026-10-09]]></link>
    <guid isPermaLink="false"><![CDATA[bonjour-la-fuite:Clinique-2026-10-09]]></guid>
    <pubDate>Fri, 09 Oct 2026 00:00:00 +0000</pubDate>
    <description><![CDATA[<ul><li>Dossiers médicaux</li></ul>]]></description>
    <category>leak</category><category>sensitive</category>
  </item>
</channel></rss>""".encode()

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
  <entry><title>Campagne d'hame&#231;onnage</title><link href="https://www.cybermalveillance.gouv.fr/a"/>
    <id>urn:a</id><updated>2026-10-11T10:00:00Z</updated><summary>Attention aux faux SMS.</summary></entry>
</feed>"""

BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""

SOURCE = news.Source(
    "test", "Test", "https://www.cert.ssi.gouv.fr/alerte/feed/", NewsItem.Kind.ALERT, ""
)
LEAK_SOURCE = next(s for s in news.SOURCES if s.kind == NewsItem.Kind.LEAK)


class FeedParsingTests(TestCase):
    def store(self, source, data):
        with mock.patch.object(news, "download", return_value=data):
            return news.refresh_source(source)

    def test_rss_items_are_stored_as_plain_text(self):
        self.assertEqual(self.store(SOURCE, RSS), 1)  # le lien javascript: est écarté
        item = NewsItem.objects.get()
        self.assertEqual(item.title, "Vulnérabilité critique dans un pare-feu")
        self.assertEqual(item.summary, "Une faille permet une exécution de code.")
        self.assertNotIn("<", item.summary)
        self.assertEqual(item.published_at.date().isoformat(), "2026-10-12")

    def test_refresh_is_idempotent(self):
        self.store(SOURCE, RSS)
        self.store(SOURCE, RSS)
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_leak_status_and_exposed_data(self):
        self.store(LEAK_SOURCE, LEAKS)
        ecole, clinique = NewsItem.objects.order_by("-published_at")
        self.assertEqual((ecole.title, ecole.confirmed, ecole.sensitive), ("École 42", True, False))
        self.assertEqual(ecole.details, ["Nom, prénom", "Adresse email"])
        self.assertEqual((clinique.confirmed, clinique.sensitive), (False, True))

    def test_leak_status_is_updated(self):
        self.store(LEAK_SOURCE, LEAKS)
        self.store(LEAK_SOURCE, LEAKS.replace("🟠 Clinique".encode(), "🟢 Clinique".encode()))
        self.assertTrue(NewsItem.objects.get(title="Clinique Exemple").confirmed)

    def test_atom_feeds_are_supported(self):
        self.store(news.Source("atom", "Atom", "", NewsItem.Kind.NEWS, ""), ATOM)
        item = NewsItem.objects.get()
        self.assertEqual(
            (item.title, item.summary), ("Campagne d'hameçonnage", "Attention aux faux SMS.")
        )

    def test_rss1_rdf_feeds_are_supported(self):
        rdf = """<?xml version="1.0" encoding="ISO-8859-15"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns="http://purl.org/rss/1.0/">
  <channel><title>LMI</title></channel>
  <item rdf:about="https://www.lemondeinformatique.fr/a.html"><title>Orange muscle son offre SecNumCloud</title>
    <link>https://www.lemondeinformatique.fr/a.html</link><description>Résumé.</description>
    <dc:date>2026-10-12T08:00:00+02:00</dc:date></item>
</rdf:RDF>""".encode("iso-8859-15")
        self.store(news.Source("lmi", "LMI", "", NewsItem.Kind.NEWS, ""), rdf)
        item = NewsItem.objects.get()
        self.assertEqual(
            (item.title, item.summary), ("Orange muscle son offre SecNumCloud", "Résumé.")
        )
        self.assertEqual(item.published_at.isoformat(), "2026-10-12T06:00:00+00:00")

    def test_entity_expansion_is_refused(self):
        with self.assertRaises(news.FeedError):
            news.parse_feed(BOMB)

    def test_only_listed_hosts_are_contacted(self):
        for url in ("https://example.com/feed", "http://www.zataz.com/feed/", "file:///etc/passwd"):
            with self.assertRaises(news.FeedError):
                news.download(url)

    def test_one_broken_source_does_not_stop_the_others(self):
        def fake_download(url):
            if "zataz" in url:
                raise news.FeedError("hors ligne")
            return RSS
        with mock.patch.object(news, "download", side_effect=fake_download):
            report = news.refresh_all()
        self.assertIsInstance(report["zataz"], news.FeedError)
        self.assertEqual(report["certfr-alertes"], 1)

    def test_old_items_are_purged(self):
        NewsItem.objects.create(source="x", guid="old", title="Ancien", url="https://a.fr",
                                published_at=timezone.now() - timedelta(days=news.KEEP_DAYS + 1))
        with mock.patch.object(news, "download", side_effect=news.FeedError("hors ligne")):
            news.refresh_all()
        self.assertFalse(NewsItem.objects.filter(guid="old").exists())


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="Un-mot-de-passe-solide-42!")
        self.client.force_login(self.user)

    def test_home_is_the_dashboard(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalogue/dashboard.html")
        self.assertContains(response, "Bonjour alice")
        self.assertContains(response, "Aucune actualité récupérée")

    def test_dashboard_shows_news_and_leaks(self):
        now = timezone.now()
        NewsItem.objects.create(
            source="certfr-alertes",
            kind="alerte",
            guid="1",
            title="Alerte pare-feu",
            url="https://www.cert.ssi.gouv.fr/a",
            published_at=now,
        )
        NewsItem.objects.create(source="zataz", kind="actu", guid="2", title="Article ZATAZ",
                                url="https://www.zataz.com/a", published_at=now)
        NewsItem.objects.create(
            source="bonjourlafuite",
            kind="fuite",
            guid="3",
            title="École 42",
            url="https://bonjourlafuite.eu.org/#a",
            published_at=now,
            confirmed=True,
            details=["Nom, prénom"],
        )
        response = self.client.get(reverse("catalogue:dashboard"))
        for text in (
            "Alerte pare-feu",
            "Article ZATAZ",
            "ZATAZ ·",
            "École 42",
            "Confirmée",
            "Nom, prénom",
        ):
            self.assertContains(response, text)

    def test_dashboard_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 302)
