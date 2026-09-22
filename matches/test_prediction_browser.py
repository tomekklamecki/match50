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

from .models import ChipAssignment, Draft, DraftPair, Match, MatchAlternative, Prediction, Round


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

    def test_dirty_exit_cancel_discard_save_and_card_navigation(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.live_server_url + '/typy/')
        rows = page.locator('[data-prediction-card]')
        dialog = page.locator('#unsaved-dialog')
        rows.first.locator('label[for$="-1"]').click()
        page.locator('[data-view=card]').click()
        expect(dialog).to_be_visible()
        dialog.locator('[data-exit=cancel]').click()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        expect(rows.first.locator('input[value="1"]')).to_be_checked()
        page.locator('[data-view=card]').click()
        dialog.locator('[data-exit=save]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        # Card navigation still saves directly, without the exit dialog.
        rows.first.locator('label[for$="-X"]').click()
        page.locator('#card-next').click()
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        expect(dialog).not_to_be_visible()
        rows.nth(1).locator('label[for$="-2"]').click()
        page.locator('[data-view=list]').click()
        expect(dialog).to_be_visible()
        dialog.locator('[data-exit=discard]').click()
        expect(rows.nth(1).locator('input[value="2"]')).not_to_be_checked()
        expect(rows.first.locator('input[value="X"]')).to_be_checked()
        page.reload()
        expect(rows.first.locator('input[value="X"]')).to_be_checked()
        # Removing TYP keeps the independent GOLE prediction.
        rows.first.locator('.goal-picker').click()
        page.locator('[data-goals="2"]').click()
        rows.first.locator('label[for$="-X"]').click()
        expect(rows.first.locator('.goal-picker')).to_have_text('2')
        page.locator('a[href="/typy/?tab=future"]').click()
        expect(dialog).to_be_visible()
        dialog.locator('[data-exit=save]').click()
        page.wait_for_url('**/typy/?tab=future')
        page.get_by_role('link', name='AKTUALNA KOLEJKA', exact=True).click()
        page.wait_for_url('**/typy/')
        expect(rows.first.locator('input[value="X"]')).not_to_be_checked()
        expect(dialog).not_to_be_visible()

    def test_list_chip_changes_share_dirty_save_and_exit_state(self):
        from playwright.sync_api import expect

        def seed():
            Prediction.objects.create(user=self.user, match=self.matches[0], predicted_result='1')
            ChipAssignment.objects.create(
                user=self.user, round=self.round, match=self.matches[0], chip='BANKER'
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        posts = []
        page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
        page.goto(self.live_server_url + '/typy/')
        row = page.locator('[data-prediction-card]').first
        save = page.locator('#save-predictions')
        dialog = page.locator('#unsaved-dialog')
        expect(save).to_be_disabled()

        # Removal is local and dirty; restoring the persisted chip is clean.
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(row.locator('.chip-picker')).to_have_text('CHIP')
        expect(save).to_be_enabled()
        self.assertEqual(posts, [])
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(save).to_be_disabled()

        # Discard restores local state and never mutates persistence.
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        page.locator('[data-view=card]').click()
        expect(dialog).to_be_visible()
        dialog.locator('[data-exit=discard]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertTrue(executor.submit(
                lambda: ChipAssignment.objects.filter(user=self.user, chip='BANKER').exists()
            ).result())

        page.locator('[data-view=list]').click()
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        page.locator('a[href="/typy/?tab=future"]').click()
        expect(dialog).to_be_visible()
        dialog.locator('[data-exit=save]').click()
        page.wait_for_url('**/typy/?tab=future')
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertFalse(executor.submit(
                lambda: ChipAssignment.objects.filter(user=self.user, chip='BANKER').exists()
            ).result())

    def test_list_chip_assignment_is_local_until_save(self):
        from playwright.sync_api import expect

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(lambda: Prediction.objects.create(
                user=self.user, match=self.matches[0], predicted_result='1'
            )).result()
        page = self.page
        posts = []
        page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
        page.goto(self.live_server_url + '/typy/')
        row = page.locator('[data-prediction-card]').first
        save = page.locator('#save-predictions')
        expect(save).to_be_disabled()
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(save).to_be_enabled()
        self.assertEqual(posts, [])
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertFalse(executor.submit(
                lambda: ChipAssignment.objects.filter(user=self.user, chip='BANKER').exists()
            ).result())
        save.click()
        expect(page.locator('#prediction-feedback')).to_contain_text('Typy zapisane')
        expect(save).to_be_disabled()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertTrue(executor.submit(
                lambda: ChipAssignment.objects.filter(user=self.user, chip='BANKER').exists()
            ).result())

    def test_list_swap_is_dirty_until_explicit_save_and_can_be_reverted(self):
        from playwright.sync_api import expect

        def seed():
            alternative = Match.objects.create(
                league='Premier League', home_team='Alternative', away_team='Away',
                kickoff=self.matches[0].kickoff,
            )
            MatchAlternative.objects.create(match=self.matches[0], alternative=alternative)

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        posts = []
        page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
        page.goto(self.live_server_url + '/typy/')
        row = page.locator('[data-prediction-card]').first
        save = page.locator('#save-predictions')
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(row.locator('.chip-picker')).to_contain_text('Swap')
        expect(save).to_be_enabled()
        self.assertEqual(posts, [])
        page.locator('a[href="/typy/?tab=future"]').click()
        expect(page.locator('#unsaved-dialog')).to_be_visible()
        page.locator('#unsaved-dialog [data-exit=cancel]').click()
        save.click()
        expect(page.locator('#prediction-feedback')).to_contain_text('Typy zapisane')
        expect(save).to_be_disabled()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertTrue(executor.submit(
                lambda: ChipAssignment.objects.filter(user=self.user, chip='SWAP').exists()
            ).result())

        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(save).to_be_enabled()
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(save).to_be_disabled()

    def test_list_swap_transition_saves_prediction_for_submitted_effective_match(self):
        from playwright.sync_api import expect

        def seed():
            alternative = Match.objects.create(
                league='Premier League', home_team='Alternative', away_team='Away',
                kickoff=self.matches[0].kickoff,
            )
            MatchAlternative.objects.create(match=self.matches[0], alternative=alternative)
            Prediction.objects.create(
                user=self.user, match=self.matches[0], predicted_result='1'
            )
            return alternative.pk

        with ThreadPoolExecutor(max_workers=1) as executor:
            alternative_id = executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        row = page.locator('[data-prediction-card]').first
        save = page.locator('#save-predictions')

        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(row.locator('[data-team-home]')).to_have_text('Alternative')
        row.locator('label[for$="-2"]').click()
        save.click()
        expect(page.locator('#prediction-feedback')).to_contain_text('Typy zapisane')
        page.reload()
        row = page.locator('[data-prediction-card]').first
        expect(row.locator('[data-team-home]')).to_have_text('Alternative')
        expect(row.locator('input[value="2"]')).to_be_checked()

        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(row.locator('[data-team-home]')).to_have_text('Home 0')
        row.locator('label[for$="-2"]').click()
        save.click()
        expect(page.locator('#prediction-feedback')).to_contain_text('Typy zapisane')
        page.reload()
        row = page.locator('[data-prediction-card]').first
        expect(row.locator('[data-team-home]')).to_have_text('Home 0')
        expect(row.locator('input[value="2"]')).to_be_checked()
        with ThreadPoolExecutor(max_workers=1) as executor:
            persisted = executor.submit(lambda: (
                Prediction.objects.get(user=self.user, match=self.matches[0]).predicted_result,
                Prediction.objects.filter(user=self.user, match_id=alternative_id).exists(),
                ChipAssignment.objects.filter(user=self.user, match=self.matches[0]).exists(),
            )).result()
        self.assertEqual(persisted, ('2', False, False))

    def test_clear_persisted_type_with_dependent_chip_saves_empty_state(self):
        from playwright.sync_api import expect

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(lambda: (
                Prediction.objects.create(user=self.user, match=self.matches[0], predicted_result='1', total_goals=2),
                ChipAssignment.objects.create(user=self.user, round=self.round, match=self.matches[0], chip='BANKER'),
            )).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        row = page.locator('[data-prediction-card]').first
        row.locator('label[for$="-1"]').click()
        expect(row.locator('.goal-picker')).to_have_text('2')
        page.locator('#prediction-form > .actions button[type=submit]').click()
        expect(page.locator('#prediction-feedback')).to_contain_text('Typy zapisane')
        page.reload()
        expect(row.locator('input[value="1"]')).not_to_be_checked()
        expect(row.locator('.goal-picker')).to_have_text('2')
        with ThreadPoolExecutor(max_workers=1) as executor:
            state = executor.submit(lambda: (
                Prediction.objects.get(user=self.user, match=self.matches[0]).predicted_result,
                Prediction.objects.get(user=self.user, match=self.matches[0]).total_goals,
                ChipAssignment.objects.filter(user=self.user, match=self.matches[0]).exists(),
            )).result()
        self.assertEqual(state, ('', 2, False))

    def test_exit_with_invalid_double_pick_stays_in_view(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.live_server_url + '/typy/?tab=future')
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        card.locator('[data-chip=DOUBLE_PICK]').click()
        card.locator('label[for$="-1"]').click()
        page.locator('[data-view=list]').click()
        page.locator('#unsaved-dialog [data-exit=save]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        expect(card.locator('.prediction-error')).to_contain_text('DOUBLE PICK')
        expect(card.locator('input[value="1"]')).to_be_checked()

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

    def test_current_and_future_tabs_always_open_in_list_view(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        page.locator('a[href="/typy/?tab=future"]').click()
        page.wait_for_url('**/typy/?tab=future')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.locator('[data-view=card]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        page.get_by_role('link', name='AKTUALNA KOLEJKA').click()
        page.wait_for_url('**/typy/')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')

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
            badge = page.locator('#league-progress > .league-filter').filter(has_text=league)
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

    def test_card_chip_states_distinguish_current_match_other_match_and_unused(self):
        from playwright.sync_api import expect

        def seed():
            for match in self.matches[:2]:
                Prediction.objects.create(user=self.user, match=match, predicted_result='1')
            ChipAssignment.objects.create(user=self.user, round=self.round, match=self.matches[0], chip='BANKER')
            ChipAssignment.objects.create(
                user=self.user, round=self.round, match=self.matches[1], chip='DOUBLE_PICK', outcomes=['1', 'X'],
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()

        page = self.page
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        first = page.locator('[data-prediction-card]').nth(0)
        banker = first.locator('[data-chip=BANKER]')
        double_pick = first.locator('[data-chip=DOUBLE_PICK]')
        expect(banker).to_have_class('chip active')
        expect(double_pick).to_have_class('chip used-elsewhere')
        expect(double_pick).to_be_disabled()

        page.locator('#card-next').click()
        second = page.locator('[data-prediction-card]').nth(1)
        expect(second.locator('[data-chip=BANKER]')).to_have_class('chip used-elsewhere')
        expect(second.locator('[data-chip=DOUBLE_PICK]')).to_have_class('chip active')
        page.locator('#card-next').click()
        third = page.locator('[data-prediction-card]').nth(2).locator('[data-chip=CHANGE_MIND]')
        expect(third).not_to_have_class('active')
        expect(third).not_to_have_class('used-elsewhere')
        expect(third).to_be_enabled()

    def test_league_badges_filter_only_list_and_card_navigation_keeps_all_matches(self):
        from playwright.sync_api import expect

        def seed():
            for i in range(2):
                Match.objects.create(
                    round=self.round, league='La Liga', home_team=f'Spanish home {i}',
                    away_team=f'Spanish away {i}', kickoff=timezone.now()+timedelta(days=2),
                )

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()

        page = self.page
        page.goto(self.live_server_url + '/typy/')
        rows = page.locator('[data-prediction-card]')
        visible_rows = page.locator('#prediction-form > [data-match-id]:visible')
        premier = page.locator('.league-filter[data-league="Premier League"]')
        la_liga = page.locator('.league-filter[data-league="La Liga"]')

        expect(rows).to_have_count(5)
        premier.click()
        expect(visible_rows).to_have_count(3)
        la_liga.click()
        expect(visible_rows).to_have_count(5)
        premier.click()
        expect(visible_rows).to_have_count(2)
        la_liga.click()
        expect(visible_rows).to_have_count(5)

        la_liga.click()
        expect(visible_rows).to_have_count(2)
        rows.nth(3).locator('.open-card').click()
        expect(page.locator('#card-position')).to_have_text('4 / 5')
        page.locator('#card-previous').click()
        expect(page.locator('#card-position')).to_have_text('3 / 5')
        expect(rows.nth(2)).to_be_visible()

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
        page.locator('#unsaved-dialog [data-exit=save]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#partial-dialog')).not_to_be_visible()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: ChipAssignment.objects.get(user=self.user, chip='DOUBLE_PICK').outcomes).result(), ['1', '2'])
        page.locator('[data-view=list]').click()
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        expect(row.locator('.chip-picker')).to_have_text('CHIP')
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Rejected"}') if route.request.method == 'POST' else route.continue_())
        row.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=DOUBLE_PICK]').click()
        page.locator('#save-predictions').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Rejected')
        expect(row.locator('.chip-picker')).to_have_text('CHIP')
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
        expect(first.locator('.chip-picker')).to_have_text('CHIP')
        self.assertEqual(len(posts), 0)
        page.locator('#save-predictions').click()
        self.assertEqual(len(posts), 1)
        second.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_enabled()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        expect(summary).to_have_attribute('title', 'Użyty: Home 1 – Away 1')
        expect(second.locator('.chip-picker')).to_contain_text('Banker')
        self.assertEqual(len(posts), 1)
        page.locator('#save-predictions').click()
        self.assertEqual(len(posts), 2)
        first.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_disabled()
        page.locator('#chip-dialog .button').click()

        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Save failed"}') if route.request.method == 'POST' else route.continue_())
        second.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        page.locator('#save-predictions').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Save failed')
        expect(second.locator('.chip-picker')).to_have_text('CHIP')
        expect(summary).not_to_have_class('used')
        first.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_enabled()
        page.locator('#chip-dialog .button').click()
        page.unroute('**/typy/')

        second.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        page.evaluate('''() => { window.realFetch = window.fetch; window.fetch = (...args) => new Promise(resolve => { window.releaseSave = () => window.realFetch(...args).then(resolve); }); }''')
        second.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        page.locator('#save-predictions').click()
        expect(page.locator('#prediction-feedback')).to_have_text('ZAPISYWANIE...')
        expect(second.locator('.chip-picker')).to_have_text('CHIP')
        expect(summary).not_to_have_class('used')
        page.evaluate('window.releaseSave()')
        expect(summary).not_to_have_class('used')
        expect(summary).to_have_attribute('title', 'Dostępny')
        page.evaluate('window.fetch = window.realFetch')
        page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Add failed"}') if route.request.method == 'POST' else route.continue_())
        first.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=BANKER]').click()
        page.locator('#save-predictions').click()
        expect(page.locator('#prediction-feedback')).to_have_text('Add failed')
        expect(first.locator('.chip-picker')).to_contain_text('Banker')
        expect(summary).to_have_class('used')
        second.locator('.chip-picker').click()
        expect(page.locator('#chip-dialog [data-chip=BANKER]')).to_be_disabled()
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
        for chip in ['BANKER', 'DOUBLE_PICK', 'CHANGE_MIND', 'SWAP', 'GOOOOOOOOAL']:
            with self.subTest(chip=chip):
                page.reload()
                first = page.locator('[data-prediction-card]').nth(0)
                second = page.locator('[data-prediction-card]').nth(1)
                summary = page.locator(f'#chip-progress [data-chip={chip}]')
                posts = []
                page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
                try:
                    first.locator('.chip-picker').click()
                    if chip == 'DOUBLE_PICK':
                        expect(page.locator('#chip-dialog input')).to_have_count(0)
                    page.locator(f'#chip-dialog [data-chip={chip}]').click()
                    if chip == 'DOUBLE_PICK': first.locator('label[for$="-X"]').click()
                    expect(summary).to_have_class('used')
                    expect(first.locator('[name^=chip_]')).to_have_value(chip)
                    self.assertEqual(len(posts), 0)
                    second.locator('.chip-picker').click()
                    expect(page.locator(f'#chip-dialog [data-chip={chip}]')).to_be_disabled()
                    page.locator('#chip-dialog .button').click()
                    if chip == 'DOUBLE_PICK': expect(first.locator('input[value=X]')).to_be_checked()
                    if chip == 'SWAP':
                        expect(first.locator('[data-team-home]')).to_have_text('Loser 0')
                        expect(first.locator('[data-team-away]')).to_have_text('Away')
                    if chip == 'GOOOOOOOOAL': expect(first.locator('input[value=X]')).to_be_disabled()
                    page.route('**/typy/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Rejected"}') if route.request.method == 'POST' else route.continue_())
                    page.locator('#save-predictions').click()
                    expect(page.locator('#prediction-feedback')).to_have_text('Rejected')
                    expect(summary).to_have_class('used')
                    expect(first.locator('[name^=chip_]')).to_have_value(chip)
                finally:
                    page.unroute('**/typy/')
                    page.reload()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: ChipAssignment.objects.filter(user=self.user).count()).result(), 0)
            self.assertFalse(executor.submit(lambda: Prediction.objects.filter(user=self.user, match=self.matches[2]).exists()).result())
        third = page.locator('[data-prediction-card]').nth(2)
        third.locator('.goal-picker').click()
        page.locator('[data-goals="9"]').click()
        expect(third.locator('.goal-input')).to_have_value('9')
        expect(third.locator('.row-status')).to_have_text('•')

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
            expect(control).to_have_attribute('aria-label', f'GOLE: {number}')
            box = control.bounding_box()
            self.assertEqual((box['x'], box['width'], box['height']), (initial['x'], initial['width'], initial['height']))
        control.click()
        page.locator('#goals-remove').click()
        expect(control).to_have_text('+ GOLE')
        control.click()
        page.locator('[data-goals="10"]').click()
        card.locator('label[for$="-1"]').click()
        page.locator('[data-view=card]').click()
        expect(control).to_have_text('10')

    def test_card_goal_button_uses_value_without_change_instruction(self):
        from playwright.sync_api import expect
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        control = card.locator('.goal-picker')
        control.click()
        page.locator('[data-goals="10"]').click()
        expect(control).to_have_text('GOLE: 10')
        expect(control).to_have_attribute('aria-label', 'GOLE: 10')
        control.click()
        expect(page.locator('#goals-dialog')).to_be_visible()

    def test_team_form_is_card_only_and_switches_with_swap(self):
        from playwright.sync_api import expect
        from .models import Team

        def seed():
            normal = Team.objects.create(name='Form Normal', api_football_id=25)
            replacement = Team.objects.create(name='Form Replacement', api_football_id=2)
            opponent = Team.objects.create(name='Form Opponent', api_football_id=1117)
            Match.objects.filter(pk=self.matches[0].pk).update(home_team_entity=normal, away_team_entity=opponent)
            alternative = Match.objects.create(league='League', home_team='Form Replacement', away_team='Form Opponent',
                home_team_entity=replacement, away_team_entity=opponent, kickoff=timezone.now()+timedelta(days=2))
            MatchAlternative.objects.create(match=self.matches[0], alternative=alternative)
            for team, score in [(normal, 2), (replacement, 0)]:
                Match.objects.create(league='History', home_team=team.name, away_team=opponent.name,
                    home_team_entity=team, away_team_entity=opponent, kickoff=timezone.now()-timedelta(days=2),
                    api_football_id=team.api_football_id, provider_status='FT', provider_score={'goals': {'home': score, 'away': 1}})
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        expect(page.locator('.team-form')).to_have_count(0)
        expect(page.locator('.national-team-flag')).to_have_count(2)
        expect(page.locator('.national-team-flag').first).to_have_text('\U0001f1e9\U0001f1ea')
        expect(page.locator('[data-team-shirt]').first).to_be_hidden()
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        expect(card.locator('.national-team-flag')).to_have_count(2)
        expect(card.locator('.national-team-flag').first).to_have_text('\U0001f1e9\U0001f1ea')
        expect(card.locator('[data-team-shirt]').first).to_be_hidden()
        dot = card.locator('.team-form-dot').first
        expect(dot).to_have_attribute('title', 'Form Normal – Form Opponent 2:1')
        dot.focus()
        expect(dot.locator('.team-form-tooltip')).to_be_visible()
        card.locator('[data-chip=SWAP]').click()
        expect(card.locator('[data-team-home]')).to_have_text('Form Replacement')
        expect(card.locator('.national-team-flag').first).to_have_text('\U0001f1eb\U0001f1f7')
        dot = card.locator('.team-form-dot').first
        expect(dot).to_have_attribute('aria-label', 'L: Form Replacement – Form Opponent 0:1')
        page.locator('[data-view=list]').click()
        expect(page.locator('.team-form')).to_have_count(0)
        expect(page.locator('.national-team-flag')).to_have_count(2)
        expect(card.locator('.national-team-flag').first).to_have_text('\U0001f1eb\U0001f1f7')
        expect(card.locator('[data-team-shirt]').first).to_be_hidden()
        card.locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(card.locator('[data-team-home]')).to_have_text('Home 0')
        expect(card.locator('.national-team-flag').first).to_have_text('\U0001f1e9\U0001f1ea')

    def test_national_flags_use_local_font_and_keep_shirt_fallbacks(self):
        from playwright.sync_api import expect
        from .models import Team
        from .services.league_flags import iso_country_flag

        countries = [(768, 'Italy', 'IT'), (1, 'Belgium', 'BE'), (25, 'Germany', 'DE'),
                     (1117, 'Greece', 'GR'), (1118, 'Netherlands', 'NL'), (775, 'Austria', 'AT')]

        def seed():
            teams = [Team.objects.create(api_football_id=pk, name=name) for pk, name, _ in countries]
            for fixture, home, away in zip(self.matches, teams[::2], teams[1::2]):
                fixture.home_team_entity, fixture.away_team_entity = home, away
                fixture.home_team, fixture.away_team = home.name, away.name
                fixture.save()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        rows = page.locator('[data-prediction-card]')
        expect(page.locator('.national-team-flag')).to_have_count(6)
        expect(rows.first.locator('[data-team-shirt]').first).to_be_hidden()
        page.locator('[data-view=card]').click()
        for index in range(3):
            card = rows.nth(index)
            expect(card.locator('.national-team-flag')).to_have_text(
                [iso_country_flag(code) for _, _, code in countries[index * 2:index * 2 + 2]])
            self.assertEqual(card.locator('.national-team-flag').first.evaluate(
                '(el) => getComputedStyle(el).fontFamily'), '"Match50 Country Flags"')
            expect(card.locator('[data-team-shirt]').first).to_be_hidden()
            if os.environ.get('MATCH50_FLAG_SCREENSHOTS'):
                card.screenshot(path=os.path.join(os.environ['MATCH50_FLAG_SCREENSHOTS'], f'flags-{index}.png'))
            if index < 2:
                page.locator('#card-next').click()
        page.locator('[data-view=list]').click()
        expect(page.locator('.national-team-flag')).to_have_count(6)
        expect(rows.first.locator('[data-team-shirt]').first).to_be_hidden()

        # A missing font must not expose regional-indicator letters on Windows.
        page.route('**/TwemojiCountryFlags.woff2', lambda route: route.abort())
        page.reload()
        page.evaluate('document.fonts.ready')
        expect(page.locator('.national-team-flag')).to_have_count(0)
        expect(rows.first.locator('[data-team-shirt]').first).to_be_visible()
        page.locator('[data-view=card]').click()
        page.evaluate('document.fonts.ready')
        expect(page.locator('.national-team-flag')).to_have_count(0)
        expect(rows.first.locator('[data-team-shirt]').first).to_be_visible()

    def test_football_association_flag_assets_render_in_list_and_card(self):
        from playwright.sync_api import expect
        from .models import Team

        associations = [(10, 'England'), (1108, 'Scotland'), (767, 'Wales'),
                        (771, 'Northern Ireland'), (1111, 'Kosovo')]

        def seed():
            teams = [Team.objects.create(api_football_id=provider_id, name=name)
                     for provider_id, name in associations]
            club = Team.objects.create(api_football_id=999999, name='Club')
            for fixture, home, away in zip(self.matches, teams[::2], teams[1::2] + [club]):
                fixture.home_team_entity, fixture.away_team_entity = home, away
                fixture.home_team, fixture.away_team = home.name, away.name
                fixture.save()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        flags = page.locator('.national-team-flag.flag-asset')
        expect(flags).to_have_count(5)
        self.assertEqual(flags.evaluate_all('(items) => items.map(item => item.getAttribute("aria-label"))'),
                         [name for _, name in associations])
        expect(page.locator('[data-prediction-card]').nth(2).locator('[data-team-shirt]').nth(1)).to_be_visible()
        page.locator('[data-view=card]').click()
        first = page.locator('[data-prediction-card]').first
        expect(first.locator('.national-team-flag.flag-asset')).to_have_count(2)
        expect(first.locator('[data-team-shirt]').first).to_be_hidden()

    def test_card_pick_hint_only_shows_without_a_selected_result(self):
        from playwright.sync_api import expect
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        hint = card.locator('.pick-hint')
        expect(hint).to_have_text('Wybierz wynik meczu')
        expect(hint).to_be_visible()
        card.locator('label[for$="-1"]').click()
        expect(hint).to_be_hidden()
        card.locator('label[for$="-1"]').click()
        expect(hint).to_be_visible()

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

    def test_goals_without_typ_save_and_navigate(self):
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
        expect(page.locator('#card-position')).to_have_text('2 / 3')
        self.assertEqual(len(posts), 1)
        page.locator('#card-previous').click()
        expect(card.locator('.goal-picker')).to_contain_text('9')
        for outcome in ('1', 'X', '2'):
            expect(card.locator(f'input[value="{outcome}"]')).not_to_be_checked()
        page.reload()
        card = page.locator('[data-prediction-card]').first
        expect(card.locator('.goal-picker')).to_have_text('9')
        for outcome in ('1', 'X', '2'):
            expect(card.locator(f'input[value="{outcome}"]')).not_to_be_checked()
        with ThreadPoolExecutor(max_workers=1) as executor:
            saved = executor.submit(
                lambda: Prediction.objects.get(user=self.user, match=self.matches[0])
            ).result()
        self.assertEqual((saved.predicted_result, saved.total_goals), ('', 9))

    def test_card_swap_without_pick_changes_fixture_and_stays_unpredicted(self):
        from playwright.sync_api import expect

        def seed():
            alternative = Match.objects.create(
                league="La Liga", home_team="Alternative home", away_team="Alternative away",
                kickoff=self.matches[0].kickoff,
            )
            MatchAlternative.objects.create(match=self.matches[0], alternative=alternative)
            return alternative.pk
        with ThreadPoolExecutor(max_workers=1) as executor:
            alternative_id = executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        card.locator('[data-chip=SWAP]').click()
        expect(page.locator('#prediction-feedback')).to_contain_text('mecz jest niezapisany')
        expect(card.locator('[data-team-home]')).to_have_text('Alternative home')
        for outcome in ('1', 'X', '2'):
            expect(card.locator(f'input[value="{outcome}"]')).not_to_be_checked()
        with ThreadPoolExecutor(max_workers=1) as executor:
            saved = executor.submit(lambda: (
                ChipAssignment.objects.filter(user=self.user, match=self.matches[0], chip='SWAP').exists(),
                Prediction.objects.filter(user=self.user, match_id=alternative_id).exists(),
            )).result()
        self.assertEqual(saved, (True, False))

    def test_card_swap_round_trip_at_ten_goals_keeps_one_effective_slot(self):
        from playwright.sync_api import expect

        def seed():
            alternative = Match.objects.create(
                league="La Liga", home_team="Alternative home", away_team="Alternative away",
                kickoff=self.matches[0].kickoff,
            )
            MatchAlternative.objects.create(match=self.matches[0], alternative=alternative)
            Prediction.objects.create(
                user=self.user, match=self.matches[0], predicted_result="1", total_goals=2,
            )
            for index in range(9):
                match = Match.objects.create(
                    round=self.round, league="Premier League", home_team=f"Boundary {index}",
                    away_team="Away", kickoff=timezone.now() + timedelta(days=2),
                )
                Prediction.objects.create(user=self.user, match=match, predicted_result="1", total_goals=1)
            return alternative.pk

        with ThreadPoolExecutor(max_workers=1) as executor:
            alternative_id = executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        expect(page.locator('#goals-progress')).to_have_text('GOLE 10/10')

        card.locator('[data-chip=SWAP]').click()
        expect(card.locator('[data-team-home]')).to_have_text('Alternative home')
        expect(card.locator('.goal-picker')).to_have_text('OBSTAW GOLE')
        card.locator('label[for$="-2"]').click()
        card.locator('.goal-picker').click()
        page.locator('[data-goals="3"]').click()
        page.locator('#card-next').click()
        expect(page.locator('#prediction-feedback')).not_to_contain_text('maximum of 10')
        page.locator('#card-previous').click()
        card = page.locator('[data-prediction-card]').first
        expect(card.locator('[data-team-home]')).to_have_text('Alternative home')
        expect(card.locator('.goal-picker')).to_have_text('GOLE: 3')

        card.locator('[data-chip=SWAP]').click()
        expect(page.locator('#prediction-feedback')).not_to_contain_text('maximum of 10')
        expect(card.locator('[data-team-home]')).to_have_text('Home 0')
        expect(card.locator('.goal-picker')).to_have_text('GOLE: 2')
        card.locator('label[for$="-X"]').click()
        card.locator('.goal-picker').click()
        page.locator('[data-goals="4"]').click()
        page.locator('#card-next').click()
        expect(page.locator('#prediction-feedback')).not_to_contain_text('maximum of 10')
        page.reload()
        page.locator('[data-view=card]').click()
        card = page.locator('[data-prediction-card]').first
        expect(card.locator('[data-team-home]')).to_have_text('Home 0')
        expect(card.locator('input[value="X"]')).to_be_checked()
        expect(card.locator('.goal-picker')).to_have_text('GOLE: 4')
        with ThreadPoolExecutor(max_workers=1) as executor:
            persisted = executor.submit(lambda: (
                Prediction.objects.filter(user=self.user, match_id=alternative_id).exists(),
                Prediction.objects.get(user=self.user, match=self.matches[0]).predicted_result,
                Prediction.objects.get(user=self.user, match=self.matches[0]).total_goals,
                Prediction.objects.filter(user=self.user, total_goals__isnull=False).count(),
            )).result()
        self.assertEqual(persisted, (False, 'X', 4, 10))

    def test_goals_counter_disables_only_empty_matches_at_ten_of_ten(self):
        from playwright.sync_api import expect

        def seed():
            for index in range(8):
                Match.objects.create(
                    round=self.round, league="Premier League", home_team=f"Extra {index}",
                    away_team="Away", kickoff=timezone.now() + timedelta(days=2),
                )
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        rows = page.locator('[data-prediction-card]')
        expect(page.locator('#goals-progress')).to_have_text('GOLE 0/10')
        for index in range(10):
            rows.nth(index).locator('.goal-picker').click()
            page.locator('[data-goals="1"]').click()
        expect(page.locator('#goals-progress')).to_have_text('GOLE 10/10')
        expect(page.locator('#goals-progress')).to_have_class('goals-progress complete')
        expect(rows.nth(10).locator('.goal-picker')).to_be_disabled()
        expect(rows.nth(0).locator('.goal-picker')).to_be_enabled()
        rows.nth(0).locator('.goal-picker').click()
        page.locator('#goals-remove').click()
        expect(page.locator('#goals-progress')).to_have_text('GOLE 9/10')
        expect(page.locator('#goals-progress')).to_have_class('goals-progress incomplete')
        expect(rows.nth(10).locator('.goal-picker')).to_be_enabled()

    def test_typy_counter_and_completion_follow_unsaved_visible_removal(self):
        from playwright.sync_api import expect

        def seed():
            for match in self.matches:
                Prediction.objects.create(user=self.user, match=match, predicted_result='1')
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(seed).result()
        page = self.page
        page.goto(self.live_server_url + '/typy/')
        progress = page.locator('#prediction-progress')
        expect(progress).to_have_text('Wytypowane: 3 / 30')
        expect(progress).to_have_class('complete')
        first = page.locator('[data-prediction-card]').first
        first.locator('label[for$="-1"]').click()
        expect(progress).to_have_text('Wytypowane: 2 / 30')
        expect(progress).to_have_class('incomplete')

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
        expect(page.locator('#chip-dialog [data-chip=DOUBLE_PICK] .chip-state')).to_have_count(0)
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
        page.locator('#unsaved-dialog [data-exit=save]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        page.locator('#card-next').click()
        page.locator('#card-next').click()
        last = page.locator('[data-prediction-card]').nth(2)
        last.locator('label[for$="-1"]').click()
        page.locator('#card-next').click()
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
        expect(rows.nth(2).locator('[data-team-home]')).to_have_text('Replacement home')
        expect(rows.nth(2).locator('[data-team-away]')).to_have_text('Replacement away')
        swap_summary = page.locator('#chip-progress [data-chip=SWAP]')
        expect(page.locator('#chip-progress .used')).to_have_count(5)
        rows.nth(2).locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(rows.nth(2).locator('[data-team-home]')).to_have_text('Home 2')
        expect(rows.nth(2).locator('[data-team-away]')).to_have_text('Away 2')
        expect(swap_summary).not_to_have_class('used')
        expect(page.locator('#chip-progress .used')).to_have_count(4)
        rows.nth(2).locator('.chip-picker').click()
        page.locator('#chip-dialog [data-chip=SWAP]').click()
        expect(rows.nth(2).locator('[data-team-home]')).to_have_text('Replacement home')
        expect(rows.nth(2).locator('[data-team-away]')).to_have_text('Replacement away')
        expect(swap_summary).to_have_class('used')
        expect(page.locator('#chip-progress .used')).to_have_count(5)
        rows.nth(6).locator('.goal-picker').click()
        page.locator('[data-goals="9"]').click()
        expect(rows.nth(6).locator('.row-status')).to_have_text('•')
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
        page.locator('#unsaved-dialog [data-exit=discard]').click()
        expect(result).not_to_be_visible()
        page.locator('[data-view=list]').click()
        expect(result).to_be_visible()
        self.assertEqual(result.evaluate('(element) => element.outerHTML'), before)
        for width in [375, 700, 900, 1440]:
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
        page.locator('#unsaved-dialog [data-exit=save]').click()
        expect(page.locator('[data-view=card]')).to_have_attribute('aria-pressed', 'true')
        expect(second.locator('input[value="1"]')).to_be_checked()
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: Prediction.objects.get(user=self.user, match=self.matches[1]).predicted_result).result(), '1')
        page.reload()
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.screenshot(path=os.path.join(tempfile.gettempdir(), 'match50-prediction-desktop.png'), full_page=True)

    def test_mobile_future_last_card_and_failed_tab_navigation(self):
        from playwright.sync_api import expect
        page = self.page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(self.live_server_url + '/typy/?tab=future')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        page.locator('[data-view=card]').click()
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
        expect(page.locator('#unsaved-dialog')).to_be_visible()
        page.locator('#unsaved-dialog [data-exit=cancel]').click()
        page.unroute('**/typy/?tab=future')
        page.locator('#card-next').click()
        expect(page.locator('#prediction-feedback')).to_contain_text('Zakończono przegląd kart')
        expect(page.locator('[data-view=list]')).to_have_attribute('aria-pressed', 'true')
        self.assertIn('tab=future', page.url)
        expect(page.locator('#prediction-progress')).to_contain_text('Wytypowane: 1/1')
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(lambda: Prediction.objects.get(match=self.future_match).total_goals).result(), 4)
        page.screenshot(path=os.path.join(tempfile.gettempdir(), 'match50-prediction-mobile.png'), full_page=True)
        for width in [375, 600, 900, 1440]:
            page.set_viewport_size({"width": width, "height": 900})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        page.locator('a[href="/typy/?tab=previous"]').click()
        page.wait_for_url('**/typy/?tab=previous')
