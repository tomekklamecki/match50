import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .admin import DraftPairAdmin
from .models import Competition, CompetitionSeason, Draft, DraftPair, Match
class DraftPairAdminPickerTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("draft-admin", "admin@example.com", "secret")
        self.client.force_login(self.admin)
        self.draft = Draft.objects.create(name="Picker Draft", starts_at=timezone.now() + timedelta(days=1))
        self.premier = Competition.objects.create(code="PL", name="Premier League", country="England")
        self.laliga = Competition.objects.create(code="LL", name="La Liga", country="Spain")
        self.premier_season = CompetitionSeason.objects.create(competition=self.premier, season_label="2030")
        self.laliga_season = CompetitionSeason.objects.create(competition=self.laliga, season_label="2030")
        self.kickoff = timezone.now() + timedelta(days=30)

    def match(self, home, competition, **kwargs):
        return Match.objects.create(
            league=competition.name,
            competition_season=self.premier_season if competition == self.premier else self.laliga_season,
            home_team=home,
            away_team=f"{home} Away",
            kickoff=self.kickoff,
            **kwargs,
        )

    def candidates(self, **params):
        response = self.client.get(
            reverse("admin:matches_draftpair_available_candidates"),
            {"draft": self.draft.pk, **params},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_effective_kw_filter_honors_manual_override(self):
        automatic = self.match("Automatic", self.premier)
        overridden = self.match("Overridden", self.premier, kw_override_year=2040, kw_override_week=40)
        automatic_week = automatic.effective_kw

        automatic_ids = {item["id"] for item in self.candidates(week=f"{automatic_week.year}-{automatic_week.week}")["candidates"]}
        override_ids = {item["id"] for item in self.candidates(week="2040-40")["candidates"]}

        self.assertIn(automatic.pk, automatic_ids)
        self.assertNotIn(overridden.pk, automatic_ids)
        self.assertEqual(override_ids, {overridden.pk})

    def test_competition_and_kw_filters_combine(self):
        premier = self.match("Arsenal", self.premier)
        self.match("Barcelona", self.laliga)
        week = premier.effective_kw
        data = self.candidates(week=f"{week.year}-{week.week}", competition=self.premier.pk)

        self.assertEqual({item["id"] for item in data["candidates"]}, {premier.pk})
        self.assertIn("Premier League", data["candidates"][0]["text"])
        self.assertIn("Arsenal", data["candidates"][0]["text"])
        self.assertIn(str(week), data["candidates"][0]["text"])

    def test_unfiltered_add_form_does_not_render_the_fixture_database(self):
        candidate = self.match("Candidate", self.premier)
        form = DraftPairAdmin.PairForm(initial={"draft": self.draft.pk})
        self.assertFalse(form.fields["match_a"].queryset.filter(pk=candidate.pk).exists())
        self.assertEqual(self.candidates()["candidates"], [])

    def test_add_page_server_renders_visible_picker_controls_and_assets(self):
        response = self.client.get(reverse("admin:matches_draftpair_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_draft"')
        self.assertContains(response, 'id="id_day_number"')
        self.assertContains(response, 'id="id_kw_filter"')
        self.assertContains(response, 'id="id_competition_filter"')
        self.assertContains(response, 'id="id_fixture_search"')
        self.assertContains(response, 'id="id_match_a"')
        self.assertContains(response, 'id="id_match_b"')
        self.assertContains(response, 'size="12"', count=2)
        self.assertContains(response, "matches/draft_pair_picker_v2.js")
        self.assertContains(response, "matches/draft_pair_admin.css")

    def test_used_and_ineligible_fixtures_are_excluded(self):
        used_a = self.match("Used A", self.premier)
        used_b = self.match("Used B", self.premier)
        available = self.match("Available", self.premier)
        DraftPair.objects.create(draft=self.draft, day_number=1, match_a=used_a, match_b=used_b)
        week = available.effective_kw
        ids = {item["id"] for item in self.candidates(week=f"{week.year}-{week.week}")["candidates"]}
        self.assertEqual(ids, {available.pk})

    def test_realistic_kw40_endpoint_all_competition_empty_and_consumed_contract(self):
        premier = self.match("KW40 Premier", self.premier, kw_override_year=2030, kw_override_week=40)
        laliga = self.match("KW40 Liga", self.laliga, kw_override_year=2030, kw_override_week=40)
        consumed = self.match("KW40 Used", self.premier, kw_override_year=2030, kw_override_week=40)
        consumed_partner = self.match("KW40 Used Partner", self.laliga, kw_override_year=2030, kw_override_week=40)
        DraftPair.objects.create(draft=self.draft, day_number=1, match_a=consumed, match_b=consumed_partner)

        all_ids = {item["id"] for item in self.candidates(week="2030-40")["candidates"]}
        premier_ids = {item["id"] for item in self.candidates(week="2030-40", competition=self.premier.pk)["candidates"]}
        empty_ids = {item["id"] for item in self.candidates(week="2099-40")["candidates"]}

        self.assertEqual(all_ids, {premier.pk, laliga.pk})
        self.assertEqual(premier_ids, {premier.pk})
        self.assertEqual(empty_ids, set())
        self.assertNotIn(consumed.pk, all_ids)

    def test_same_match_is_rejected_by_authoritative_model_validation(self):
        candidate = self.match("Duplicate", self.premier)
        with self.assertRaises(ValidationError):
            DraftPair.objects.create(draft=self.draft, day_number=1, match_a=candidate, match_b=candidate)

    def test_pair_string_is_human_readable(self):
        first = self.match("Home One", self.premier)
        second = self.match("Home Two", self.premier)
        pair = DraftPair.objects.create(draft=self.draft, day_number=1, match_a=first, match_b=second)
        self.assertEqual(str(pair), "Home One - Home One Away ↔ Home Two - Home Two Away")


@skipUnless(os.environ.get("MATCH50_BROWSER_TESTS") == "1", "Opt-in Playwright browser checks")
class DraftPairAdminBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright

        self.admin = get_user_model().objects.create_superuser("picker-browser", "browser@example.com", "secret")
        self.client.force_login(self.admin)
        self.draft = Draft.objects.create(name="KW Picker Draft", starts_at=timezone.now() + timedelta(days=1))
        competition = Competition.objects.create(code="BPL", name="Browser Premier League", country="England")
        season = CompetitionSeason.objects.create(competition=competition, season_label="2030")
        kickoff = timezone.now() + timedelta(days=30)
        self.first = Match.objects.create(league=competition.name, competition_season=season, home_team="Arsenal Browser", away_team="Chelsea Browser", kickoff=kickoff)
        self.second = Match.objects.create(league=competition.name, competition_season=season, home_team="Liverpool Browser", away_team="Everton Browser", kickoff=kickoff + timedelta(hours=2))
        self.competition = competition
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="msedge", headless=True)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.context.add_cookies([{"name": "sessionid", "value": self.client.cookies["sessionid"].value, "url": self.live_server_url}])
        self.page = self.context.new_page()
        self.errors = []
        self.response_urls = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("response", lambda response: self.response_urls.append(response.url))

    def tearDown(self):
        self.context.close()
        self.browser.close()
        self.pw.stop()
        self.assertEqual(self.errors, [])

    def test_filter_select_save_and_reopen_pair(self):
        page = self.page
        page.goto(f"{self.live_server_url}{reverse('admin:matches_draftpair_add')}")
        for selector in ("#id_draft", "#id_day_number", "#id_kw_filter", "#id_competition_filter", "#id_fixture_search", "#id_match_a", "#id_match_b"):
            self.assertTrue(page.locator(selector).is_visible(), selector)

        page.locator("#id_draft").select_option(str(self.draft.pk))
        week_value = f"{self.first.effective_kw.year}-{self.first.effective_kw.week}"
        page.locator(f'#id_kw_filter option[value="{week_value}"]').wait_for(state="attached")
        page.locator("#id_kw_filter").select_option(week_value)
        page.locator("#id_competition_filter").select_option(str(self.competition.pk))
        page.locator("#id_fixture_search").fill("Arsenal")
        page.locator(f'#id_match_a option[value="{self.first.pk}"]').wait_for(state="attached")
        page.locator("#id_match_a").select_option(str(self.first.pk))
        page.locator("#id_fixture_search").fill("")
        page.locator(f'#id_match_b option[value="{self.second.pk}"]').wait_for(state="attached")
        page.locator("#id_match_b").select_option(str(self.second.pk))
        page.locator('input[name="_save"]').click()
        page.wait_for_url("**/admin/matches/draftpair/")

        with ThreadPoolExecutor() as executor:
            pair = executor.submit(lambda: DraftPair.objects.get(draft=self.draft)).result()
        page.goto(f"{self.live_server_url}{reverse('admin:matches_draftpair_change', args=[pair.pk])}")
        self.assertEqual(page.locator("#id_match_a").input_value(), str(self.first.pk))
        self.assertEqual(page.locator("#id_match_b").input_value(), str(self.second.pk))
        self.assertTrue(any("draft_pair_picker_v2.js" in url for url in self.response_urls))
        self.assertTrue(any("draft_pair_admin.css" in url for url in self.response_urls))
        self.assertTrue(any("available-candidates" in url for url in self.response_urls))
