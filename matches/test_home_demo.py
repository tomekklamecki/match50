import os
from datetime import timedelta
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from matches.models import Match, Team
from matches.services.homepage_demo import DEMO_FIXTURES, homepage_demo


def seed_history():
    for provider_id, names, team_ids, score in zip(DEMO_FIXTURES,
            [('Norway', 'Sweden'), ('Wales', 'Northern Ireland'), ('Poland', 'Ukraine')],
            [(1090, 5), (767, 771), (24, 772)], [(3, 1), (1, 1), (0, 2)]):
        teams = [Team.objects.create(name=name, api_football_id=pk) for name, pk in zip(names, team_ids)]
        Match.objects.create(api_football_id=provider_id, league='INTERNATIONAL', home_team=names[0], away_team=names[1],
            home_team_entity=teams[0], away_team_entity=teams[1], kickoff=timezone.now()-timedelta(days=100),
            provider_status='FT', provider_score={'goals': {'home': score[0], 'away': score[1]}})


def domain_snapshot():
    return {model._meta.label: list(model.objects.order_by('pk').values())
            for model in apps.get_app_config('matches').get_models()}


class HomeDemoTests(TestCase):
    def setUp(self):
        seed_history()

    def test_anonymous_home_is_read_only_and_uses_completed_provider_history(self):
        before = domain_snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-demo-match=', count=3)
        self.assertEqual([m.api_football_id for m in response.context['demo_matches']], list(DEMO_FIXTURES))
        self.assertTrue(all(m.provider_status == 'FT' for m in response.context['demo_matches']))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.assertEqual(before, domain_snapshot())
        self.assertEqual(response.context['demo_facts'], [
            {'home': 3, 'away': 1, 'outcome': '1', 'modifier': 1},
            {'home': 1, 'away': 1, 'outcome': 'X', 'modifier': 0},
            {'home': 0, 'away': 2, 'outcome': '2', 'modifier': 0}])
        self.assertEqual(homepage_demo()[1], homepage_demo()[1])

    def test_missing_or_unfinished_history_does_not_invent_results(self):
        Match.objects.filter(api_football_id=DEMO_FIXTURES[0]).update(provider_status='NS')
        self.assertEqual(homepage_demo(), ([], []))
        self.assertNotContains(self.client.get('/'), 'data-demo-match=')

    def test_authenticated_home_preserves_domain_rows(self):
        user = get_user_model().objects.create_user(username='demo-player')
        self.client.force_login(user)
        before = domain_snapshot()
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(before, domain_snapshot())


@skipUnless(os.environ.get('MATCH50_BROWSER_TESTS') == '1', 'Opt-in Playwright browser checks')
class HomeDemoBrowserTests(StaticLiveServerTestCase):
    def test_desktop_mobile_play_retry_and_no_writes(self):
        from playwright.sync_api import sync_playwright, expect
        seed_history()
        before = domain_snapshot()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            for width in (1280, 390):
                page = browser.new_page(viewport={'width': width, 'height': 900})
                errors, requests = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append((request.method, request.url)))
                page.goto(self.live_server_url + '/')
                requests.clear()
                rows = page.locator('[data-demo-match]')
                expect(rows.first.locator('[data-disabled-chip]')).to_have_count(3)
                rows.first.locator('[data-disabled-chip]').first.click(force=True)
                expect(rows.first.locator('.demo-locked-chip-hint')).to_have_css('opacity', '1')
                page.locator('#demo-check').click()
                expect(page.locator('#demo-result')).to_be_hidden()
                rows.nth(0).locator('[data-pick="1"]').click()
                rows.nth(0).locator('.demo-goal-picker').click()
                page.locator('#demo-goals-dialog [data-demo-goals="4"]').click()
                rows.nth(0).locator('[data-chip="BANKER"]').click()
                expect(rows.nth(1).locator('[data-chip="BANKER"]')).to_be_disabled()
                rows.nth(1).locator('[data-chip="DOUBLE_PICK"]').click()
                rows.nth(1).locator('[data-pick="1"]').click()
                rows.nth(1).locator('[data-pick="X"]').click()
                expect(page.locator('#demo-chips-counter')).to_have_text('2 / 2 CHIPY')
                rows.nth(1).locator('.demo-goal-picker').click()
                page.locator('#demo-goals-dialog [data-demo-goals="2"]').click()
                rows.nth(2).locator('[data-pick="2"]').click()
                expect(rows.nth(2).locator('.demo-goal-picker')).to_be_disabled()
                page.locator('#demo-check').click()
                expect(page.locator('#demo-totals')).to_have_text('TYPY3GOLE2BONUS3TOTAL8')
                expect(rows.nth(0).locator('.resolved-score')).to_have_text('3 : 1')
                expect(rows.nth(0).locator('[data-pick="1"]')).to_have_attribute('data-resolved-state', 'hit')
                expect(rows.nth(1).locator('[data-pick="1"]')).to_have_attribute('data-resolved-state', 'covered')
                expect(rows.nth(1).locator('[data-pick="X"]')).to_have_attribute('data-resolved-state', 'hit')
                expect(rows.nth(0).locator('.demo-goal-picker')).to_have_attribute('data-resolved-state', 'hit')
                expect(rows.nth(0).locator('.resolved-earned')).to_have_text('+5 PKT')
                expect(rows.nth(0).locator('.demo-gm-seal')).to_have_text('GM')
                expect(page.locator('.demo-gm-seal')).to_have_count(1)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.locator('#demo-retry').click()
                expect(page.locator('#demo-counter')).to_have_text('0 / 3 TYPY')
                expect(page.locator('#demo-goals-counter')).to_have_text('0 / 2 GOLE')
                expect(page.locator('#demo-chips-counter')).to_have_text('0 / 2 CHIPY')
                expect(page.locator('#demo-result')).to_be_hidden()
                self.assertEqual(page.locator('[aria-pressed="true"]').count(), 0)
                self.assertEqual(page.locator('[data-resolved-state]').count(), 0)
                # A miss with BANKER loses one bonus point; exact goals remain independent.
                for i in range(3):
                    rows.nth(i).locator('[data-pick="X"]').click()
                rows.nth(0).locator('[data-chip="BANKER"]').click()
                rows.nth(2).locator('[data-chip="DOUBLE_PICK"]').click()
                rows.nth(2).locator('[data-pick="1"]').click()
                rows.nth(0).locator('.demo-goal-picker').click()
                page.locator('#demo-goals-dialog [data-demo-goals="0"]').click()
                page.locator('#demo-check').click()
                expect(page.locator('#demo-totals')).to_have_text('TYPY1GOLE0BONUS-1TOTAL0')
                expect(rows.nth(0).locator('[data-pick="X"]')).to_have_attribute('data-resolved-state', 'miss')
                expect(rows.nth(0).locator('.demo-goal-picker')).to_have_attribute('data-resolved-state', 'miss')
                expect(rows.nth(2).locator('[data-pick="X"]')).to_have_attribute('data-resolved-state', 'miss')
                expect(rows.nth(2).locator('[data-pick="1"]')).to_have_attribute('data-resolved-state', 'miss')
                self.assertEqual(requests, [])
                self.assertEqual(errors, [])
                page.close()
            browser.close()
        self.assertEqual(before, domain_snapshot())
