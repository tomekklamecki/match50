"""Optional real-browser checks: set MATCH50_BROWSER_TESTS=1 and install Playwright.

Uses the installed Edge browser and Django's isolated test database.
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone

from .models import ChipAssignment, Draft, DraftPair, Match, Prediction, Round


@skipUnless(os.environ.get("MATCH50_BROWSER_TESTS") == "1", "Opt-in Playwright browser checks")
class PredictionBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.user = get_user_model().objects.create_user(username="browser")
        self.round = Round.objects.create(name="Browser current", is_active=True)
        self.future = Round.objects.create(name="Browser future")
        Draft.objects.create(name="Browser draft", starts_at=timezone.now(), next_round=self.future)
        self.matches = [Match.objects.create(round=self.round, league="Premier League", home_team=f"Home {i}",
            away_team=f"Away {i}", kickoff=timezone.now()+timedelta(days=2)) for i in range(3)]
        self.future_match = Match.objects.create(round=self.future, league="La Liga", home_team="Future home",
            away_team="Future away", kickoff=timezone.now()+timedelta(days=5))
        self.client.force_login(self.user)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="msedge", headless=True)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.context.add_cookies([{"name": "sessionid", "value": self.client.cookies["sessionid"].value, "url": self.live_server_url}])
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close(); self.browser.close(); self.pw.stop()
        self.assertEqual(self.errors, [])

    def confirm_partial(self):
        self.page.locator('#partial-save').click()

    def test_direct_card_navigation_saves_without_completion_prompt_in_current_and_future(self):
        from playwright.sync_api import expect
        def seed():
            return Match.objects.create(round=self.future, league='Serie A', home_team='Second future', away_team='Away', kickoff=timezone.now()+timedelta(days=6)).id
        with ThreadPoolExecutor(max_workers=1) as executor: future_id = executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        rows = page.locator('[data-prediction-card]')
        rows.nth(0).locator('label[for$="-1"]').click()
        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Save failed"}') if route.request.method == 'POST' else route.continue_())
        rows.nth(2).locator('.open-card').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Save failed')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        expect(rows.nth(0).locator('input[value="1"]')).to_be_checked()
        page.unroute('**/typy/')
        page.evaluate('''() => { window.realFetch=window.fetch; window.fetch=(...args)=>new Promise(resolve=>{ window.releaseSave=()=>window.realFetch(...args).then(resolve); }); }''')
        rows.nth(2).locator('.open-card').click()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.evaluate('window.releaseSave()')
        expect(page.locator('#card-position')).to_have_text('3 / 3')
        expect(rows.nth(2)).to_be_visible()
        expect(page.locator('#partial-dialog')).not_to_be_visible()
        page.evaluate('window.fetch=window.realFetch')
        self.assertEqual(page.url, self.live_server_url + '/typy/')
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: Prediction.objects.get(user=self.user, match=self.matches[0]).predicted_result).result(), '1')
        page.goto(self.live_server_url + '/typy/?tab=future')
        page.locator('[data-prediction-card]').first.locator('label[for$="-2"]').click()
        page.locator(f'[data-match-id="{future_id}"] .open-card').click()
        expect(page.locator('#card-position')).to_have_text('2 / 2 dostępnych')
        expect(page.locator(f'[data-match-id="{future_id}"]')).to_be_visible()
        expect(page.locator('#partial-dialog')).not_to_be_visible()
        self.assertIn('tab=future', page.url)

    def test_flags_and_kickoffs_are_consistent_across_views_and_summary(self):
        from playwright.sync_api import expect
        identities = [('Premier League', 'England'), ('La Liga', 'Spain'), ('Serie A', 'Italy'),
                      ('Bundesliga', 'Germany'), ('Ligue 1', 'France'), ('Ekstraklasa', 'Poland'), ('Champions League', None)]
        def seed():
            for league, _country in identities[1:]:
                Match.objects.create(round=self.round, league=league, home_team='Short', away_team='A longer opponent name', kickoff=timezone.now()+timedelta(days=2))
        with ThreadPoolExecutor(max_workers=1) as executor: executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        for league, country in identities:
            row = page.locator('[data-prediction-card]').filter(has=page.locator('.match-league', has_text=league)).first
            expect(row.locator('.league-kickoff')).to_be_visible()
            expected = row.evaluate('(row)=>JSON.parse(document.getElementById("prediction-state").textContent).slots[row.dataset.matchId].original.kickoff')
            expect(row.locator('.league-kickoff')).to_have_text(expected)
            self.assertGreater(row.locator('.league-kickoff').bounding_box()['y'], row.locator('.match-league').bounding_box()['y'])
            badge = page.locator('#league-progress > span').filter(has_text=league)
            if country:
                expect(row.locator(f'.league-flag[aria-label="{country}"]')).to_have_count(1)
                expect(badge.locator(f'.league-flag[aria-label="{country}"]')).to_have_count(1)
            else:
                expect(row.locator('.league-flag')).to_have_count(0)
                expect(badge.locator('.league-flag')).to_have_count(0)
            row.locator('.open-card').click()
            expect(row).to_be_visible()
            expect(row.locator('.league-kickoff')).not_to_be_visible()
            if country: expect(row.locator(f'.league-flag[aria-label="{country}"]')).to_have_count(1)
            page.locator('[data-view=list]').click()
        expect(page.locator('[aria-label="United Kingdom"]')).to_have_count(0)

    def test_double_pick_is_prepared_only_in_main_row_then_uses_existing_save(self):
        from playwright.sync_api import expect
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        row = page.locator('[data-prediction-card]').first
        row.locator('label[for$="-1"]').click()
        row.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip]')).to_have_count(5)
        expect(page.locator('#chip-dialog input')).to_have_count(0)
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        expect(page.locator('#chip-dialog')).not_to_be_visible()
        expect(row.locator('.chip-picker')).to_contain_text('wybierz 2')
        expect(page.locator('#chip-progress [data-chip=DOUBLE_PICK]')).not_to_have_class('used')
        row.locator('label[for$="-X"]').click()
        expect(page.locator('#chip-progress [data-chip=DOUBLE_PICK]')).to_have_class('used')
        expect(row.locator('input[value="1"]')).to_be_checked()
        expect(row.locator('input[value=X]')).to_be_checked()
        row.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog input')).to_have_count(0)
        expect(page.locator('#chip-dialog [data-chip=DOUBLE_PICK]')).to_have_attribute('aria-pressed', 'true')
        page.locator('#chip-dialog .button').click()
        row.locator('label[for$="-X"]').click()
        row.locator('label[for$="-2"]').click()
        page.locator('[data-view=card]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#partial-dialog')).not_to_be_visible()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: ChipAssignment.objects.get(user=self.user, chip='DOUBLE_PICK').outcomes).result(), ['1', '2'])
        page.locator('[data-view=list]').click()
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        expect(row.locator('.chip-picker')).to_have_text('◇ CHIP')
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Rejected"}') if route.request.method == 'POST' else route.continue_())
        row.locator('label[for$="-X"]').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Rejected')
        expect(row.locator('.chip-picker')).to_have_text('◇ CHIP')
        expect(row.locator('input[value=X]')).not_to_be_checked()
        expect(row.locator('input[value="1"]')).to_be_checked()
        expect(page.locator('#chip-progress [data-chip=DOUBLE_PICK]')).not_to_have_class('used')

    def test_list_chip_summary_tracks_only_successful_saves(self):
        from playwright.sync_api import expect
        def seed():
            for match in self.matches[:2]:
                Prediction.objects.create(user=self.user, match=match, predicted_result='1')
            ChipAssignment.objects.create(user=self.user, round=self.round, match=self.matches[0], chip='BANKER')
        with ThreadPoolExecutor(max_workers=1) as executor: executor.submit(seed).result()
        page = self.page
        posts = []
        page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
        page.goto(self.live_server_url + '/typy/')
        first, second = page.locator('[data-prediction-card]').nth(0), page.locator('[data-prediction-card]').nth(1)
        summary = page.locator('#chip-progress [data-chip=BANKER]')
        expect(summary).to_have_class('used')
        first.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(summary).not_to_have_class('used')
        expect(first.locator('.chip-picker')).to_have_text('◇ CHIP')
        self.assertEqual(len(posts), 1)
        second.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_enabled()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(summary).to_have_attribute('title', 'Użyty: Home 1 – Away 1')
        expect(second.locator('.chip-picker')).to_contain_text('Banker')
        self.assertEqual(len(posts), 2)
        first.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_disabled()
        page.locator('#chip-dialog .button').click()

        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Save failed"}') if route.request.method == 'POST' else route.continue_())
        second.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Save failed')
        expect(second.locator('.chip-picker')).to_contain_text('Banker')
        expect(summary).to_have_class('used')
        expect(page.locator('#chips-counter')).to_have_text('CHIPS 1/5')
        first.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_disabled()
        page.locator('#chip-dialog .button').click()
        page.unroute('**/typy/')

        page.evaluate('''() => { window.realFetch = window.fetch; window.fetch = (...args) => new Promise(resolve => { window.releaseSave = () => window.realFetch(...args).then(resolve); }); }''')
        second.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(page.locator('#prediction-feedback')).to_have_text('ZAPISYWANIE...')
        expect(second.locator('.chip-picker')).to_contain_text('Banker')
        expect(summary).to_have_class('used')
        page.evaluate('window.releaseSave()')
        expect(summary).not_to_have_class('used')
        expect(summary).to_have_attribute('title', 'Dostępny')
        expect(page.locator('#chips-counter')).to_have_text('CHIPS 0/5')
        page.evaluate('window.fetch = window.realFetch')
        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Add failed"}') if route.request.method == 'POST' else route.continue_())
        first.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Add failed')
        expect(first.locator('.chip-picker')).to_have_text('◇ CHIP')
        expect(summary).not_to_have_class('used')
        second.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_enabled()
        page.locator('#chip-dialog .button').click()
        self.assertEqual(page.url, self.live_server_url + '/typy/')

    def test_list_picker_persists_all_five_chip_types_without_navigation(self):
        from playwright.sync_api import expect
        def seed():
            Round.objects.filter(pk=self.round.pk).update(is_active=False)
            for index, match in enumerate(self.matches[:2]):
                Prediction.objects.create(user=self.user, match=match, predicted_result='1')
                loser = Match.objects.create(league='La Liga', home_team=f'Loser {index}', away_team='Away', kickoff=timezone.now()+timedelta(days=3))
                draft = Draft.objects.create(name=f'Origin {index}', starts_at=timezone.now(), is_active=False)
                DraftPair.objects.create(draft=draft, day_number=1, match_a=match, match_b=loser, winner=match, resolution_method='VOTE', resolved_at=timezone.now())
            Round.objects.filter(pk=self.round.pk).update(is_active=True)
        with ThreadPoolExecutor(max_workers=1) as executor: executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        first, second = page.locator('[data-prediction-card]').nth(0), page.locator('[data-prediction-card]').nth(1)
        third = page.locator('[data-prediction-card]').nth(2)
        third.locator('.goal-picker').click()
        page.locator('[data-goals="9"]').click()
        for chip in ['BANKER', 'DOUBLE_PICK', 'CHANGE_MIND', 'SWAP', 'GOOOOOOOOAL']:
            with self.subTest(chip=chip):
                summary = page.locator(f'#chip-progress [data-chip={chip}]')
                first.locator('.chip-picker').click()
                if chip == 'DOUBLE_PICK':
                    expect(page.locator('#chip-dialog input')).to_have_count(0)
                page.locator(f'#chip-dialog [data-chip={chip}]').click()
                if chip == 'DOUBLE_PICK': first.locator('label[for$="-X"]').click()
                expect(summary).to_have_class('used')
                expect(first.locator('[name^=chip_]')).to_have_value(chip)
                second.locator('.chip-picker').click()
                expect(page.locator(f'#chip-dialog [data-chip={chip}]')).to_be_disabled()
                page.locator('#chip-dialog .button').click()
                if chip == 'DOUBLE_PICK': expect(first.locator('input[value=X]')).to_be_checked()
                if chip == 'SWAP': expect(first.locator('.teams')).to_have_text('Loser 0 – Away')
                if chip == 'GOOOOOOOOAL': expect(first.locator('input[value=X]')).to_be_disabled()
                page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Rejected"}') if route.request.method == 'POST' else route.continue_())
                first.locator('.chip-picker').click()
                page.locator(f'#chip-dialog [data-chip={chip}]').click()
                expect(page.locator('#prediction-feedback')).to_have_text('Rejected')
                expect(summary).to_have_class('used')
                expect(first.locator('[name^=chip_]')).to_have_value(chip)
                if chip == 'DOUBLE_PICK': expect(first.locator('input[value=X]')).to_be_checked()
                if chip == 'SWAP': expect(first.locator('.teams')).to_have_text('Loser 0 – Away')
                page.unroute('**/typy/')
                first.locator('.chip-picker').click()
                page.locator(f'#chip-dialog [data-chip={chip}]').click()
                expect(summary).not_to_have_class('used')
                expect(first.locator('.chip-picker')).to_have_text('◇ CHIP')
                second.locator('.chip-picker').click()
                expect(page.locator(f'#chip-dialog [data-chip={chip}]')).to_be_enabled()
                page.locator('#chip-dialog .button').click()
                self.assertEqual(page.url, self.live_server_url + '/typy/')
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: ChipAssignment.objects.filter(user=self.user).count()).result(), 0)
            self.assertFalse(executor.submit(lambda: Prediction.objects.filter(user=self.user, match=self.matches[2]).exists()).result())
        expect(third.locator('.goal-input')).to_have_value('9')
        expect(third.locator('.row-status')).to_have_text('!')

    def test_list_goal_numbers_keep_dimensions_and_card_label(self):
        from playwright.sync_api import expect
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        card = page.locator('[data-prediction-card]').first
        control = card.locator('.goal-picker')
        expect(control).to_have_text('+ GOLE')
        initial = control.bounding_box()
        for number in [1, 9, 10, 0, 15]:
            control.click()
            page.locator(f'[data-goals="{number}"]').click()
            expect(control).to_have_text(str(number))
            expect(control).to_have_attribute('aria-label', f'GOLE: {number} — zmień')
            box = control.bounding_box()
            self.assertEqual((box['x'], box['width'], box['height']), (initial['x'], initial['width'], initial['height']))
        control.click()
        page.locator('#goals-remove').click()
        expect(control).to_have_text('+ GOLE')
        control.click()
        page.locator('[data-goals="10"]').click()
        card.locator('label[for$="-1"]').click()
        page.locator('[data-view=card]').click()
        expect(control).to_have_text('GOLE: 10 · ZMIEŃ')

    def test_clean_navigation_and_reverted_edits_make_no_requests(self):
        from playwright.sync_api import expect
        page = self.page
        posts = []
        page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        page.locator('#card-next').click()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        page.locator('#card-previous').click()
        expect(page.locator('#card-position')).to_have_text('1 / 3')
        card = page.locator('[data-prediction-card]').first
        card.locator('label[for$="-1"]').click()
        card.locator('label[for$="-1"]').click()
        card.locator('.goal-picker').click()
        page.locator('[data-goals="9"]').click()
        card.locator('.goal-picker').click()
        page.locator('#goals-remove').click()
        card.locator('[data-chip=DOUBLE_PICK]').click()
        card.locator('[data-chip=DOUBLE_PICK]').click()
        page.locator('[data-view=list]').click()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.locator('[data-view=card]').click()
        page.locator('#card-next').click()
        page.locator('#card-next').click()
        page.locator('#card-next').click()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.locator('a[href="/typy/?tab=future"]').click()
        page.wait_for_url('**/typy/?tab=future')
        page.locator('a[href="/typy/?tab=previous"]').click()
        page.wait_for_url('**/typy/?tab=previous')
        self.assertEqual(posts, [])

    def test_incomplete_inline_validation_and_optional_goals(self):
        from playwright.sync_api import expect
        page = self.page
        posts = []
        page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        card.locator('.goal-picker').click()
        page.locator('[data-goals="9"]').click()
        page.locator('#card-next').click()
        expect(card.locator('.prediction-error')).to_contain_text('Wybierz typ 1 / X / 2')
        expect(card.locator('input[value="1"]')).to_have_attribute('aria-invalid', 'true')
        expect(card.locator('.goal-picker')).to_contain_text('9')
        expect(page.locator('#card-position')).to_have_text('1 / 3')
        self.assertEqual(posts, [])
        card.locator('.goal-picker').click()
        page.locator('#goals-remove').click()
        card.locator('[data-chip=CHANGE_MIND]').click()
        page.locator('[data-view=list]').click()
        expect(card.locator('.prediction-error')).to_be_visible()
        self.assertEqual(posts, [])
        card.locator('label[for$="-1"]').click()
        expect(card.locator('.prediction-error')).to_be_hidden()
        page.locator('#card-next').click()
        self.confirm_partial()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        saved_count = len(posts)
        page.locator('#card-previous').click()
        page.locator('[data-view=list]').click()
        expect(card.locator('.row-status')).to_have_text('✓')
        expect(card.locator('.goal-picker')).to_have_text('+ GOLE')
        page.locator('[data-view=card]').click()
        page.locator('#card-next').click()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        self.assertEqual(len(posts), saved_count)

    def test_compact_pickers_double_pick_and_current_finish(self):
        from playwright.sync_api import expect
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        first = page.locator('[data-prediction-card]').nth(0)
        second = page.locator('[data-prediction-card]').nth(1)
        first.locator('label[for$="-1"]').click()
        first.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        first.locator('label[for$="-X"]').click()
        expect(first.locator('input[value="2"]')).to_be_disabled()
        self.assertNotIn('double-available', first.locator('input[value="2"]').locator('..').get_attribute('class'))
        first.locator('label[for$="-X"]').click()
        expect(first.locator('input[value="2"]')).to_be_enabled()
        self.assertIn('double-available', first.locator('input[value="2"]').locator('..').get_attribute('class'))
        first.locator('label[for$="-X"]').click()
        first.locator('.goal-picker').click()
        expect(page.locator('#goals-dialog')).to_have_class('anchored-picker')
        page.locator('[data-goals="15"]').click()
        second.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=DOUBLE_PICK]')).to_be_disabled()
        expect(page.locator('#chip-dialog [data-chip=DOUBLE_PICK] .chip-state')).to_contain_text('Użyty:')
        page.locator('#chip-dialog .button').click()
        second.locator('label[for$="-X"]').click()
        second.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=GOOOOOOOOAL]')).to_be_disabled()
        page.locator('#chip-dialog .button').click()
        second.locator('label[for$="-2"]').click()
        second.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=GOOOOOOOOAL]').click()
        expect(second.locator('input[value=X]')).to_be_disabled()
        page.locator('[data-view=card]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        page.locator('#card-next').click()
        page.locator('#card-next').click()
        last = page.locator('[data-prediction-card]').nth(2)
        last.locator('label[for$="-1"]').click()
        page.locator('#card-next').click()
        self.confirm_partial()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        self.assertEqual(page.url, self.live_server_url + '/typy/')
        expect(first.locator('.goal-picker')).to_have_text('15')
        expect(first.locator('.row-status')).to_have_text('✓')
        expect(last.locator('.row-status')).to_have_text('✓')
        page.reload()
        page.locator('[data-view=list]').click()
        expect(first.locator('input[value="X"]')).to_be_checked()
        expect(first.locator('.goal-picker')).to_have_text('15')

    def test_review_columns_density_swap_and_result_card_isolation(self):
        from playwright.sync_api import expect
        def fixtures():
            for i in range(3, 15):
                self.matches.append(Match.objects.create(round=self.round, league='Very long competition name' if i % 2 else 'Serie A',
                    home_team='A very long home team name for alignment testing' if i % 2 else 'Juve',
                    away_team='Another very long away team name' if i % 2 else 'Napoli', kickoff=timezone.now()+timedelta(days=2)))
            for i, chip in enumerate(['BANKER', 'DOUBLE_PICK', 'SWAP', 'GOOOOOOOOAL', 'CHANGE_MIND']):
                kwargs = {}
                target = self.matches[i]
                if chip == 'SWAP':
                    target = Match.objects.create(league='La Liga', home_team='Replacement home', away_team='Replacement away', kickoff=timezone.now()+timedelta(days=3))
                    origin = Draft.objects.create(name='Origin', starts_at=timezone.now(), is_active=False)
                    Round.objects.filter(pk=self.round.pk).update(is_active=False)
                    DraftPair.objects.create(draft=origin, day_number=1, match_a=self.matches[i], match_b=target, winner=self.matches[i], resolution_method='VOTE', resolved_at=timezone.now())
                    Round.objects.filter(pk=self.round.pk).update(is_active=True)
                    kwargs['replacement_match'] = target
                if chip == 'DOUBLE_PICK': kwargs['outcomes'] = ['1', 'X']
                if chip == 'GOOOOOOOOAL': kwargs['goal_team'] = self.matches[i].home_team
                ChipAssignment.objects.create(user=self.user, round=self.round, match=self.matches[i], chip=chip, **kwargs)
                Prediction.objects.create(user=self.user, match=target, predicted_result='1', total_goals=3 if i % 2 else None)
            Match.objects.create(round=self.round, league='Finished', home_team='Result home', away_team='Result away', kickoff=timezone.now()-timedelta(days=2), home_goals=1, away_goals=0)
        with ThreadPoolExecutor(max_workers=1) as executor: executor.submit(fixtures).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        result = page.locator('.current-match-row[data-current-state=finished]')
        before = result.evaluate('(element) => element.outerHTML')
        self.assertEqual(result.evaluate('(element) => getComputedStyle(element).display'), 'grid')
        expect(result.locator('.current-score')).to_contain_text('1:0')
        expect(result.locator('.current-points')).to_be_visible()
        expect(result.locator('.history-row')).to_have_count(0)
        rows = page.locator('[data-prediction-card]')
        expect(rows.nth(2).locator('.teams')).to_have_text('Replacement home – Replacement away')
        swap_summary = page.locator('#chip-progress [data-chip=SWAP]')
        expect(page.locator('#chip-progress .used')).to_have_count(5)
        rows.nth(2).locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(rows.nth(2).locator('.teams')).to_have_text('Home 2 – Away 2')
        expect(swap_summary).not_to_have_class('used')
        expect(page.locator('#chip-progress .used')).to_have_count(4)
        rows.nth(2).locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(rows.nth(2).locator('.teams')).to_have_text('Replacement home – Replacement away')
        expect(swap_summary).to_have_class('used')
        expect(page.locator('#chip-progress .used')).to_have_count(5)
        rows.nth(6).locator('.goal-picker').click()
        page.locator('[data-goals="9"]').click()
        expect(rows.nth(6).locator('.row-status')).to_have_text('!')
        expect(rows.nth(7).locator('.row-status')).to_have_text('○')
        for selector in ['.teams', '.choices', '.choice:nth-child(1) label', '.choice:nth-child(2) label', '.choice:nth-child(3) label', '.goal-picker', '.chip-picker', '.row-status', '.open-card']:
            positions = rows.evaluate_all('(rows, selector) => rows.map(row => row.querySelector(selector).getBoundingClientRect().x)', selector)
            self.assertLess(max(positions) - min(positions), 1)
        self.assertLessEqual(max(rows.evaluate_all('(rows) => rows.map(row => row.getBoundingClientRect().height)')), 58)
        rows.nth(0).evaluate('(row) => window.scrollTo(0, row.getBoundingClientRect().top + scrollY - 175)')
        visible = rows.evaluate_all('(rows) => rows.filter(row => {const r=row.getBoundingClientRect();return r.top>=175 && r.bottom<=innerHeight}).length')
        self.assertGreaterEqual(visible, 8)
        page.screenshot(path=os.path.join(tempfile.gettempdir(), 'match50-review-desktop.png'), full_page=True)
        rows.nth(6).locator('.goal-picker').click()
        page.locator('#goals-remove').click()
        page.locator('[data-view=card]').click()
        expect(result).not_to_be_visible()
        page.locator('[data-view=list]').click()
        expect(result).to_be_visible()
        self.assertEqual(result.evaluate('(element) => element.outerHTML'), before)
        for width in [320, 375, 700, 900, 1440]:
            page.set_viewport_size({'width': width, 'height': 900})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        page.set_viewport_size({'width': 375, 'height': 812})
        page.screenshot(path=os.path.join(tempfile.gettempdir(), 'match50-review-mobile.png'), full_page=True)

    def test_save_before_navigation_errors_and_shared_views(self):
        from playwright.sync_api import expect
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.locator('[data-view=card]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        first = page.locator(f'[data-match-id="{self.matches[0].id}"]')
        first.locator('label[for$="-1"]').click()
        first.locator('[data-chip=DOUBLE_PICK]').click()
        page.locator('#card-next').click()
        expect(first.locator('.prediction-error')).to_contain_text('DOUBLE PICK')
        expect(page.locator('#card-position')).to_have_text('1 / 3')
        expect(first.locator('input[value="1"]')).to_be_checked()
        first.locator('label[for$="-X"]').click()
        first.locator('.goal-picker').click()
        page.locator('[data-goals="3"]').click()
        page.locator('#card-next').click()
        self.confirm_partial()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        second = page.locator(f'[data-match-id="{self.matches[1].id}"]')
        expect(second.locator('[data-chip=DOUBLE_PICK]')).to_be_disabled()
        second.locator('label[for$="-2"]').click()

        # Hold a response to prove that navigation waits for success.
        page.evaluate('''() => { window.realFetch = window.fetch; window.fetch = (...args) => new Promise(resolve => { window.releaseSave = () => window.realFetch(...args).then(resolve); }); }''')
        page.locator('#card-next').click()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        expect(page.locator('#prediction-feedback')).to_have_text('ZAPISYWANIE...')
        expect(page.locator('#card-next')).to_be_disabled()
        page.evaluate('window.releaseSave()')
        expect(page.locator('#card-position')).to_have_text('3 / 3')
        page.evaluate('window.fetch = window.realFetch')
        page.locator('#card-previous').click()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        expect(second.locator('input[value="2"]')).to_be_checked()
        page.locator('[data-view=list]').click()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        expect(first.locator('input[value="1"]')).to_be_checked()
        expect(first.locator('input[value="X"]')).to_be_checked()
        expect(first.locator('.goal-picker')).to_have_text('3')
        second.locator('label[for$="-1"]').click()
        page.locator('[data-view=card]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        expect(second.locator('input[value="1"]')).to_be_checked()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: Prediction.objects.get(user=self.user, match=self.matches[1]).predicted_result).result(), '1')
        page.reload()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        page.screenshot(path=os.path.join(tempfile.gettempdir(), 'match50-prediction-desktop.png'), full_page=True)

    def test_mobile_future_last_card_and_failed_tab_navigation(self):
        from playwright.sync_api import expect
        page = self.page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(self.live_server_url + '/typy/?tab=future')
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#card-position')).to_have_text('1 / 1 dostępnych')
        expect(page.locator('#prediction-progress')).to_contain_text('Dostępne: 1/30')
        expect(page.locator('#card-next')).to_have_text('✓ ZAPISZ I ZAKOŃCZ')
        card = page.locator('[data-prediction-card]')
        card.locator('label[for$="-1"]').click()
        card.locator('[data-chip=GOOOOOOOOAL]').click()
        expect(card.locator('input[value=X]')).to_be_disabled()
        card.locator('.goal-picker').click()
        expect(page.locator('#goals-dialog')).to_be_visible()
        page.locator('[data-goals="4"]').click()
        page.route('**/typy/?tab=future', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Test save failed"}') if route.request.method == 'POST' else route.continue_())
        page.locator('#card-next').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Test save failed')
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        page.locator('a[href="/typy/?tab=previous"]').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Test save failed')
        expect(card.locator('input[value="1"]')).to_be_checked()
        self.assertIn('tab=future', page.url)
        page.unroute('**/typy/?tab=future')
        page.locator('#card-next').click()
        self.confirm_partial()
        expect(page.locator('#prediction-feedback')).to_contain_text('Zakończono przegląd kart')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        self.assertIn('tab=future', page.url)
        expect(page.locator('#prediction-progress')).to_contain_text('Wytypowane: 1/1')
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: Prediction.objects.get(match=self.future_match).total_goals).result(), 4)
        page.screenshot(path=os.path.join(tempfile.gettempdir(), 'match50-prediction-mobile.png'), full_page=True)
        for width in [320, 375, 600, 900, 1440]:
            page.set_viewport_size({"width": width, "height": 900})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        page.locator('a[href="/typy/?tab=previous"]').click()
        page.wait_for_url('**/typy/?tab=previous')
