"""Export du planning de lecture vers un agenda (fichier .ics)."""
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import calendar_export, reading
from .models import Guide, StudyPlan


class CalendarExportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("lea")
        self.client.force_login(self.user)
        self.guides = [
            Guide.objects.create(
                title=f"Guide n°{n}, avec ; virgule", slug=f"guide-{n}", estimated_minutes=40
            )
            for n in range(3)
        ]
        for guide in self.guides:
            reading.add(self.user, guide)

    def get_ics(self):
        response = self.client.get(reverse("catalogue:study_plan_ics"))
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        return response.content.decode("utf-8")

    def test_one_event_per_reading_day_at_the_chosen_time(self):
        StudyPlan.objects.create(
            user=self.user, minutes_per_day=45, weekdays=list(range(7)), reading_time=time(18, 0)
        )
        ics = self.get_ics()
        self.assertTrue(
            ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
            and ics.endswith("END:VCALENDAR\r\n")
        )
        self.assertEqual(
            ics.count("BEGIN:VEVENT"), 3
        )  # 3 guides de 40 min, 45 min par jour : 3 jours
        today = timezone.localdate()
        start = timezone.make_aware(timezone.datetime.combine(today, time(18, 0)))
        self.assertIn(f"DTSTART:{calendar_export._utc(start)}", ics)
        self.assertIn(f"DTEND:{calendar_export._utc(start + timedelta(minutes=40))}", ics)
        self.assertIn(f"UID:cyberlearning-lecture-{self.user.pk}-{today:%Y%m%d}@testserver", ics)
        self.assertIn(r"Guide n°0\, avec \; virgule", ics)  # échappement RFC 5545
        self.assertIn("http://testserver/guides/guide-0/", ics.replace("\r\n ", ""))
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_only_reading_days_are_exported(self):
        weekday = timezone.localdate().weekday()
        StudyPlan.objects.create(user=self.user, minutes_per_day=120, weekdays=[(weekday + 1) % 7])
        ics = self.get_ics()
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 1)
        self.assertIn(f"-{tomorrow:%Y%m%d}@testserver\r\n", ics)

    def test_long_guide_events_last_the_daily_time(self):
        Guide.objects.filter(slug="guide-0").update(estimated_minutes=200)
        StudyPlan.objects.create(
            user=self.user, minutes_per_day=60, weekdays=list(range(7)), reading_time=time(9, 0)
        )
        ics = self.get_ics()
        start = timezone.make_aware(timezone.datetime.combine(timezone.localdate(), time(9, 0)))
        self.assertIn(
            f"DTEND:{calendar_export._utc(start + timedelta(minutes=60))}", ics
        )  # et non 200 min
        self.assertIn("suite de", ics)

    def test_requires_a_plan_and_a_login(self):
        self.assertEqual(self.client.get(reverse("catalogue:study_plan_ics")).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("catalogue:study_plan_ics")).status_code, 302)

    def test_long_lines_are_folded(self):
        folded = calendar_export._fold("DESCRIPTION:" + "é" * 100)
        parts = folded.split("\r\n ")
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p.encode("utf-8")) <= 75 for p in parts))
        self.assertEqual("".join(parts), "DESCRIPTION:" + "é" * 100)
