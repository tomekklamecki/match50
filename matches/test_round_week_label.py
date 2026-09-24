from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from django.template import Context, Template
from django.test import SimpleTestCase

from matches.models import Match
from matches.services.calendar_week import CalendarWeek, round_week_label


class RoundWeekLabelTests(SimpleTestCase):
    def round_with(self, *matches):
        return SimpleNamespace(matches=Mock(all=Mock(return_value=matches)))

    def test_week_ranges_and_year_boundary(self):
        self.assertEqual(CalendarWeek(2026, 40).date_range_label, '28.09–04.10')
        self.assertEqual(CalendarWeek(2026, 39).date_range_label, '21.09–27.09')
        self.assertEqual(CalendarWeek(2020, 53).date_range_label, '28.12–03.01')

    def test_effective_override_and_local_kickoff_drive_label(self):
        match = Match(kickoff=datetime(2026, 9, 27, 22, 30, tzinfo=timezone.utc))
        round_ = self.round_with(match)
        self.assertEqual(round_week_label(round_), '28.09–04.10')
        match.kw_override_year, match.kw_override_week = 2026, 39
        self.assertEqual(round_week_label(round_), '21.09–27.09')

    def test_legacy_multiple_weeks_are_sorted_and_deduplicated(self):
        matches = [SimpleNamespace(effective_kw=CalendarWeek(2026, week)) for week in (40, 39, 40)]
        self.assertEqual(round_week_label(self.round_with(*matches)), '21.09–27.09 · 28.09–04.10')

    def test_empty_round_does_not_invent_dates(self):
        self.assertEqual(round_week_label(None), '')
        self.assertEqual(round_week_label(self.round_with()), '')

    def test_shared_template_tag(self):
        round_ = self.round_with(SimpleNamespace(effective_kw=CalendarWeek(2026, 40)))
        template = Template('{% load prediction_labels %}{% round_week_label round %}')
        self.assertEqual(template.render(Context({'round': round_})), '28.09–04.10')
