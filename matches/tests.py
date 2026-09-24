from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from unittest.mock import patch

from .models import Achievement, ChipAssignment, Competition, CompetitionSeason, Draft, DraftPair, DraftVote, GlobalModifier, Match, Match50Season, MatchKickoffSnapshot, Prediction, Round, Team, TrophyFinish, UserAchievement, UserRoundScore
from .services.scoring import recalculate_round_scores, recalculate_user_round_score
from .services.player_statistics import calculate_player_statistics
from .services.rankings import month_ranking, round_ranking, season_ranking, top_with_current
from .services.achievements import evaluate_snapshot_achievements, evaluate_trophies
from .services.profile import current_performance, profile_data, public_history, round_history_summaries
from .services.effective_match import draft_loser_for_winner
from .management.commands.seed_dev_data import Command as SeedDevDataCommand


class StageOnePredictionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="player", password="secret")
        self.round = Round.objects.create(name="MVP", is_active=True)
        self.match = self.create_match(1)
        self.client.force_login(self.user)

    def create_match(self, number, kickoff=None):
        return Match.objects.create(
            round=self.round,
            league="League",
            home_team=f"Home {number}",
            away_team=f"Away {number}",
            kickoff=kickoff or timezone.now() + timedelta(days=1),
        )

    def post_prediction(self, match, result="1", goals="", **extra):
        payload = {f"result_{match.id}": result, f"goals_{match.id}": goals}
        payload.update(extra)
        return self.client.post(reverse("typy"), payload)

    def test_thirty_match_active_round_can_be_created_and_displayed(self):
        self.assertEqual(self.round.match_count, 30)
        for number in range(2, 31):
            self.create_match(number)
        response = self.client.get(reverse("typy"))
        self.assertContains(response, "Home 30")
        self.assertEqual(len(response.context["matches"]), 30)

    def test_saves_and_edits_prediction_before_kickoff(self):
        response = self.post_prediction(self.match, "1", "3", confirm_less_than_ten="1")
        self.assertRedirects(response, reverse("typy"))
        prediction = Prediction.objects.get(user=self.user, match=self.match)
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("1", 3))

        response = self.post_prediction(self.match, "2", "4", confirm_less_than_ten="1")
        self.assertRedirects(response, reverse("typy"))
        prediction.refresh_from_db()
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("2", 4))

    def test_post_kickoff_edit_is_blocked_by_backend(self):
        self.match.kickoff = timezone.now() - timedelta(seconds=1)
        self.match.save()
        Prediction.objects.create(user=self.user, match=self.match, predicted_result="1", total_goals=2)

        response = self.post_prediction(self.match, "2", "3", confirm_less_than_ten="1")
        self.assertEqual(response.status_code, 400)
        prediction = Prediction.objects.get(user=self.user, match=self.match)
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("1", 2))

    def test_goal_predictions_are_limited_to_ten_per_round(self):
        matches = [self.match] + [self.create_match(number) for number in range(2, 12)]
        payload = {}
        for match in matches:
            payload[f"result_{match.id}"] = "1"
            payload[f"goals_{match.id}"] = "2"
        response = self.client.post(reverse("typy"), payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Prediction.objects.exists())

    def test_fewer_than_ten_goal_predictions_require_confirmation(self):
        response = self.post_prediction(self.match, "1", "2")
        self.assertContains(response, "Nie wybrałeś 10 meczów")
        self.assertFalse(Prediction.objects.exists())
        response = self.post_prediction(self.match, "1", "2", confirm_less_than_ten="1")
        self.assertRedirects(response, reverse("typy"))

    def test_clear_button_only_changes_the_form_until_save(self):
        Prediction.objects.create(user=self.user, match=self.match, predicted_result="1", total_goals=2)
        response = self.client.get(reverse("typy"))
        self.assertContains(response, "WYCZYŚĆ WSZYSTKIE TYPY")
        self.assertContains(response, "input.checked=false")
        self.assertContains(response, "input:checked+label")
        self.assertTrue(Prediction.objects.filter(user=self.user, match=self.match).exists())

    def test_goals_entered_mark_match_finished_and_derive_result(self):
        self.match.home_goals = 2
        self.match.away_goals = 1
        self.match.save()
        self.match.refresh_from_db()
        self.assertEqual(self.match.result, "1")
        self.assertEqual(self.match.status, Match.Status.FINISHED)


class StageOneNavigationTests(TestCase):
    def setUp(self):
        Round.objects.create(name="MVP", is_active=True)

    def test_all_stage_one_navigation_routes_are_public_and_work(self):
        for name in ("home", "typy", "draft", "rankings", "stats", "rules"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class StageTwoDraftTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="voter", password="secret")
        self.draft = Draft.objects.create(
            name="Community Draft", starts_at=timezone.now() - timedelta(hours=12), next_round_name="Round 2"
        )

    def candidate(self, number):
        return Match.objects.create(
            league="Premier League", home_team=f"Home {number}", away_team=f"Away {number}",
            kickoff=timezone.now() + timedelta(days=10),
        )

    def pair(self, day=None, number=1):
        return DraftPair.objects.create(
            draft=self.draft, day_number=day, match_a=self.candidate(number * 2), match_b=self.candidate(number * 2 + 1)
        )

    def create_complete_draft(self):
        pairs = []
        for day in range(1, 7):
            for slot in range(1, 6):
                number = (day - 1) * 5 + slot
                pairs.append(self.pair(number=number))
        return pairs

    def test_sixty_candidates_form_thirty_pairs_and_days_are_assigned_automatically(self):
        pairs = self.create_complete_draft()
        self.assertEqual(len(pairs), 30)
        self.draft.validate_ready()
        self.assertEqual(len({match_id for pair in pairs for match_id in (pair.match_a_id, pair.match_b_id)}), 60)
        self.assertEqual([pair.day_number for pair in pairs], [day for day in range(1, 7) for _ in range(5)])
        from .admin import DraftPairAdmin
        day_field = DraftPairAdmin.PairForm().fields["day_number"]
        self.assertFalse(day_field.disabled)
        self.assertNotIn("readonly", day_field.widget.attrs)
        self.assertNotIn("disabled", day_field.widget.attrs)

    def test_duplicate_candidate_and_thirty_first_pair_are_rejected(self):
        first = self.pair()
        with self.assertRaises(ValidationError):
            DraftPair.objects.create(draft=self.draft, day_number=1, match_a=first.match_a, match_b=self.candidate(99))
        for number in range(2, 31):
            self.pair(number=(number + 1))
        with self.assertRaises(ValidationError):
            self.pair(number=99)

    def test_active_round_and_used_candidates_are_unavailable_for_new_pairs(self):
        active_round = Round.objects.create(name="Typy", is_active=True)
        active_match = Match.objects.create(
            round=active_round, league="League", home_team="Active Home", away_team="Active Away",
            kickoff=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            DraftPair.objects.create(
                draft=self.draft, day_number=1, match_a=active_match, match_b=self.candidate(300)
            )

        used_pair = self.pair()
        from .admin import DraftPairAdmin
        form = DraftPairAdmin.PairForm(initial={"draft": self.draft.pk})
        available_ids = set(form.fields["match_a"].queryset.values_list("id", flat=True))
        self.assertNotIn(active_match.id, available_ids)
        self.assertNotIn(used_pair.match_a_id, available_ids)
        self.assertNotIn(used_pair.match_b_id, available_ids)

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("admin:matches_draftpair_available_candidates"), {"draft": self.draft.pk}
        )
        self.assertEqual(response.status_code, 200)
        ajax_ids = {candidate["id"] for candidate in response.json()["candidates"]}
        self.assertNotIn(active_match.id, ajax_ids)
        self.assertNotIn(used_pair.match_a_id, ajax_ids)
        self.assertNotIn(used_pair.match_b_id, ajax_ids)

    def test_vote_once_ratio_privacy_and_authentication(self):
        pair = self.pair()
        response = self.client.get(reverse("draft"))
        self.assertContains(response, "?%")
        response = self.client.post(reverse("draft_vote", args=[pair.id]), {"selected_match": pair.match_a_id})
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.user)
        response = self.client.post(reverse("draft_vote", args=[pair.id]), {"selected_match": pair.match_a_id})
        self.assertRedirects(response, reverse("draft"))
        response = self.client.get(reverse("draft"))
        self.assertContains(response, "100%")
        self.assertContains(response, "selected")
        self.assertEqual(self.client.post(reverse("draft_vote", args=[pair.id]), {"selected_match": pair.match_b_id}).status_code, 400)
        vote = DraftVote.objects.get(user=self.user, pair=pair)
        vote.selected_match = pair.match_b
        with self.assertRaises(ValidationError):
            vote.save()

    def test_closed_pair_resolves_votes_and_persists_random_tie(self):
        pair = self.pair()
        first = get_user_model().objects.create_user(username="first", password="secret")
        second = get_user_model().objects.create_user(username="second", password="secret")
        DraftVote.objects.create(user=first, pair=pair, selected_match=pair.match_a)
        DraftVote.objects.create(user=second, pair=pair, selected_match=pair.match_a)
        self.draft.starts_at = timezone.now() - timedelta(days=2)
        self.draft.save()
        pair.draft.refresh_from_db()
        pair.resolve()
        self.assertEqual(pair.winner_id, pair.match_a_id)
        self.assertEqual(pair.resolution_method, DraftPair.Resolution.VOTE)

        tie_pair = DraftPair.objects.create(draft=self.draft, day_number=2, match_a=self.candidate(200), match_b=self.candidate(201))
        tie_pair.resolve()
        winner_id = tie_pair.winner_id
        self.assertEqual(tie_pair.resolution_method, DraftPair.Resolution.RANDOM_TIE)
        tie_pair.resolve()
        self.assertEqual(tie_pair.winner_id, winner_id)
        tie_pair.day_number = 3
        with self.assertRaises(ValidationError):
            tie_pair.save()

    def test_draft_page_countdown_closes_day_and_persists_winner(self):
        pair = self.pair()
        voter = get_user_model().objects.create_user(username="countdown-voter", password="secret")
        DraftVote.objects.create(user=voter, pair=pair, selected_match=pair.match_b)
        self.client.force_login(self.user)
        response = self.client.get(reverse("draft"))
        self.assertContains(response, 'id="draft-countdown"')

        self.draft.starts_at = timezone.now() - timedelta(days=1, seconds=1)
        self.draft.save()
        self.draft.resolve_closed_pairs()
        pair.refresh_from_db()
        self.assertEqual(pair.winner_id, pair.match_b_id)
        self.assertEqual(pair.resolution_method, DraftPair.Resolution.VOTE)

    def test_draft_pairs_use_shared_team_visuals_for_future_teams(self):
        pair = self.pair()
        england = Team.objects.create(name="Draft England", api_football_id=10)
        germany = Team.objects.create(name="Draft Germany", api_football_id=25)
        club = Team.objects.create(name="Draft Club", api_football_id=999999, shirt_primary="#112233")
        pair.match_a.home_team_entity = england
        pair.match_a.away_team_entity = club
        pair.match_a.save(update_fields=["home_team_entity", "away_team_entity"])
        pair.match_b.home_team_entity = germany
        pair.match_b.save(update_fields=["home_team_entity"])

        response = self.client.get(reverse("draft"))

        self.assertContains(response, "football-flags/england.svg")
        self.assertContains(response, "🇩🇪")
        self.assertContains(response, 'data-team-shirt')
        self.assertNotContains(response, ">GB-ENG<")

    def test_thirty_winners_populate_next_round_and_keep_losers(self):
        self.draft.starts_at = timezone.now() - timedelta(days=7)
        self.draft.save()
        pairs = self.create_complete_draft()
        for pair in pairs:
            pair.resolve()
        modifiers = [
            GlobalModifier.objects.create(code=f"MOD_{number}", name=f"Modifier {number}", description="Test")
            for number in range(3)
        ]
        self.draft.modifier_options.set(modifiers)
        self.draft.winning_modifier = modifiers[0]
        self.draft.save(update_fields=["winning_modifier"])
        next_round = self.draft.populate_next_round()
        self.assertEqual(next_round.matches.count(), 30)
        self.assertEqual(Match.objects.filter(round__isnull=True).count(), 30)
        self.draft.refresh_from_db()
        self.assertTrue(self.draft.is_active)
        self.assertTrue(all(pair.winner_id in {pair.match_a_id, pair.match_b_id} for pair in DraftPair.objects.filter(draft=self.draft)))

    def test_completed_draft_stays_active_and_day_six_is_read_only(self):
        self.draft.starts_at = timezone.now() - timedelta(days=7)
        self.draft.save()
        self.create_complete_draft()
        modifiers = [
            GlobalModifier.objects.create(code=f"FINAL_{number}", name=f"Final {number}", description="Test")
            for number in range(3)
        ]
        self.draft.modifier_options.set(modifiers)
        self.client.force_login(self.user)

        response = self.client.get(reverse("draft"))

        self.draft.refresh_from_db()
        self.assertTrue(self.draft.is_active)
        self.assertEqual(self.draft.next_round.matches.count(), 30)
        self.assertContains(response, "DAY 6 / 6")
        self.assertContains(response, "DRAFT ZAKOŃCZONY")
        self.assertNotContains(response, 'id="draft-countdown"')
        self.assertNotContains(response, ">WYBIERZ</button>")


class StageThreeChipTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="chips", password="secret")
        self.round = Round.objects.create(name="Chips", is_active=True)
        self.matches = [Match.objects.create(round=self.round, league="L", home_team=f"H{i}", away_team=f"A{i}", kickoff=timezone.now() + timedelta(days=1)) for i in range(2)]

    def test_chip_is_unique_per_type_and_match(self):
        ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=self.matches[0])
        with self.assertRaises(ValidationError):
            ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=self.matches[1])
        with self.assertRaises(ValidationError):
            ChipAssignment.objects.create(user=self.user, round=self.round, chip="GOOOOOOOOAL", match=self.matches[0], goal_team=self.matches[0].home_team)

    def test_double_pick_and_goal_team_validation(self):
        with self.assertRaises(ValidationError):
            ChipAssignment.objects.create(user=self.user, round=self.round, chip="DOUBLE_PICK", match=self.matches[0], outcomes=["1"])
        chip = ChipAssignment.objects.create(user=self.user, round=self.round, chip="DOUBLE_PICK", match=self.matches[0], outcomes=["1", "X"])
        self.assertEqual(chip.outcomes, ["1", "X"])
        with self.assertRaises(ValidationError):
            ChipAssignment.objects.create(user=self.user, round=self.round, chip="GOOOOOOOOAL", match=self.matches[1], goal_team="Other")


class FinishedMatchCardTests(TestCase):
    def test_resolved_reward_badge_only_for_earned_modifier_bonus(self):
        from django.template.loader import render_to_string
        from matches.services.match_cards import prepare_settled_match
        match = self.match('Home', 'Away')
        prediction = Prediction.objects.create(user=self.user, match=match, predicted_result='1', total_goals=4)
        self.finish(match, 3, 1)
        for bonus in (0, 1):
            prepare_settled_match(match, prediction=prediction, score_info={
                'standard_correct': True, 'goal_correct': True,
                'typy': 1, 'gole': 1, 'bonus': bonus, 'modifier_bonus': bonus,
            })
            html = render_to_string('matches/_resolved_prediction_card.html', {'match': match})
            self.assertEqual('class="resolved-gm-seal"' in html, bool(bonus))
            self.assertNotIn('resolved-modifier', html)
            self.assertIn(f'+{2 + bonus} PKT', html)
            self.assertIn('class="resolved-ball"', html)

    def test_losing_double_pick_marks_both_selected_outcomes_red(self):
        match = self.match('Home', 'Away')
        ChipAssignment.objects.create(user=self.user, round=self.round, chip='DOUBLE_PICK', match=match, outcomes=['1', 'X'])
        Prediction.objects.create(user=self.user, match=match, predicted_result='1')
        self.finish(match, 0, 2)
        recalculate_user_round_score(self.user, self.round)
        response = self.client.get(reverse('typy'))
        self.assertContains(response, 'data-resolved-state="miss"', count=2)
        self.assertNotContains(response, 'data-resolved-state="covered"')

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="history", password="secret")
        self.round = Round.objects.create(name="History", is_active=True)
        self.client.force_login(self.user)

    def match(self, home, away):
        return Match.objects.create(
            round=self.round, league="League", home_team=home, away_team=away,
            kickoff=timezone.now() + timedelta(days=1),
        )

    def finish(self, match, home, away):
        match.home_goals, match.away_goals = home, away
        match.save()

    def test_finished_card_keeps_independent_standard_and_goal_states(self):
        match = self.match("Alpha", "Beta")
        Prediction.objects.create(user=self.user, match=match, predicted_result="1", total_goals=2)
        self.finish(match, 2, 1)
        recalculate_round_scores(self.round)

        response = self.client.get(reverse("typy"))
        self.assertContains(response, "Alpha — Beta")
        self.assertContains(response, "2:1")
        self.assertContains(response, 'data-current-state="finished"')
        self.assertContains(response, 'data-resolved-state="hit"')
        self.assertContains(response, 'data-resolved-state="miss"')
        score = self.user.round_scores.get(round=self.round)
        self.assertEqual(score.total_points, 1)

    def test_finished_card_shows_red_empty_goal_box_when_goals_were_not_predicted(self):
        match = self.match("No Goals Home", "No Goals Away")
        Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        self.finish(match, 1, 0)
        recalculate_user_round_score(self.user, self.round)

        response = self.client.get(reverse("typy"))
        self.assertContains(response, 'class="resolved-goals"')
        self.assertNotContains(response, 'data-resolved-state="miss"')

    def test_prediction_toolbar_follows_existing_match_editability(self):
        settled = self.match("Settled Home", "Settled Away")
        upcoming = self.match("Upcoming Home", "Upcoming Away")
        self.finish(settled, 1, 0)

        response = self.client.get(reverse("typy"))
        self.assertFalse(response.context["round_is_closed"])
        self.assertTrue(response.context["has_editable_matches"])
        self.assertContains(response, 'id="prediction-toolbar"')
        self.assertContains(response, '<button type="button" class="button secondary" id="clear-predictions">')
        self.assertContains(response, '<button class="button lime" id="save-predictions" type="submit" disabled>ZAPISZ</button>')
        self.assertContains(response, 'input[type=checkbox]:not(:disabled)')

        self.finish(upcoming, 1, 0)
        response = self.client.get(reverse("typy"))
        self.assertTrue(response.context["round_is_closed"])
        self.assertFalse(response.context["has_editable_matches"])
        self.assertContains(response, 'id="prediction-toolbar"')
        self.assertNotContains(response, 'data-view="card"')
        self.assertNotContains(response, '<button type="button" class="button secondary" id="clear-predictions">')
        self.assertNotContains(response, 'id="save-predictions"')

        locked = self.match("Locked Home", "Locked Away")
        locked.kickoff = timezone.now() - timedelta(minutes=5)
        locked.save(update_fields=["kickoff"])
        response = self.client.get(reverse("typy"))
        self.assertFalse(response.context["round_is_closed"])
        self.assertFalse(response.context["has_editable_matches"])
        self.assertContains(response, 'id="prediction-toolbar"')
        self.assertNotContains(response, 'data-view="card"')

    def test_current_round_mixes_editable_locked_and_finished_compact_rows(self):
        editable = self.match("Editable Home", "Editable Away")
        locked = self.match("Locked Home", "Locked Away")
        finished = self.match("Finished Home", "Finished Away")
        Prediction.objects.create(user=self.user, match=locked, predicted_result="2")
        locked.kickoff = timezone.now() - timedelta(minutes=5)
        locked.save()
        self.finish(finished, 2, 0)

        response = self.client.get(reverse("typy"))
        self.assertContains(response, f'data-match-id="{editable.id}" data-prediction-card="true"')
        self.assertContains(response, 'data-current-state="locked"')
        self.assertContains(response, 'data-current-state="finished"')
        self.assertContains(response, "Locked Home — Locked Away")
        self.assertContains(response, "Finished Home — Finished Away")
        self.assertEqual(response.content.decode().count('class="history-row"'), 0)

        change_mind = ChipAssignment.objects.create(user=self.user, round=self.round, chip="CHANGE_MIND", match=editable)
        editable.kickoff = timezone.now() - timedelta(minutes=5)
        editable.save()
        response = self.client.get(reverse("typy"))
        self.assertTrue(response.context["has_editable_matches"])
        self.assertContains(response, 'id="prediction-toolbar"')
        self.assertContains(response, f'data-match-id="{editable.id}" data-prediction-card="true"')
        change_mind.refresh_from_db()
        self.assertTrue(change_mind.prediction_editable)

    def test_double_pick_and_chip_history_are_rendered_after_finish(self):
        match = self.match("Gamma", "Delta")
        ChipAssignment.objects.create(
            user=self.user, round=self.round, chip="DOUBLE_PICK", match=match, outcomes=["1", "X"]
        )
        Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        self.finish(match, 0, 0)
        recalculate_user_round_score(self.user, self.round)

        response = self.client.get(reverse("typy"))
        self.assertContains(response, "DOUBLE PICK")
        self.assertContains(response, "1 + X")
        self.assertContains(response, 'data-resolved-state="hit"')
        self.assertContains(response, 'data-resolved-state="covered"')

    def test_finished_cards_keep_banker_change_mind_and_goal_chip_configuration(self):
        banker = self.match("Banker Home", "Banker Away")
        mind = self.match("Mind Home", "Mind Away")
        goal = self.match("Goal Home", "Goal Away")
        ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=banker)
        ChipAssignment.objects.create(user=self.user, round=self.round, chip="CHANGE_MIND", match=mind)
        ChipAssignment.objects.create(user=self.user, round=self.round, chip="GOOOOOOOOAL", match=goal, goal_team="Goal Home")
        for match in (banker, mind, goal):
            Prediction.objects.create(user=self.user, match=match, predicted_result="1")
            self.finish(match, 2, 0)
        recalculate_user_round_score(self.user, self.round)

        response = self.client.get(reverse("typy"))
        self.assertContains(response, "BANKER")
        self.assertContains(response, "VAR")
        self.assertContains(response, "GOOOOL!")
        self.assertContains(response, "Goal Home")
        self.assertContains(response, 'id="chips-progress"')

    def test_swap_card_uses_replacement_teams_result_and_scoring_state(self):
        original = self.match("Original Home", "Original Away")
        replacement = Match.objects.create(
            league="League", home_team="Replacement Home", away_team="Replacement Away",
            kickoff=timezone.now() + timedelta(days=1),
        )
        assignment = ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=original)
        # The SWAP relationship itself is validated by Stage 3A.  This focused
        # presentation test seeds its already-persisted shape without needing a
        # full 30-pair Draft fixture.
        ChipAssignment.objects.filter(pk=assignment.pk).update(chip="SWAP", replacement_match=replacement)
        Prediction.objects.create(user=self.user, match=replacement, predicted_result="1", total_goals=4)
        self.finish(original, 0, 0)
        self.finish(replacement, 3, 1)
        recalculate_user_round_score(self.user, self.round)

        response = self.client.get(reverse("typy"))
        self.assertContains(response, "Replacement Home — Replacement Away")
        self.assertContains(response, "3:1")
        self.assertContains(response, "Oryginalnie: Original Home — Original Away")
        self.assertContains(response, 'class="resolved-score" aria-label="Wynik końcowy">3:1</strong>')
        score = self.user.round_scores.get(round=self.round)
        self.assertEqual((score.typy_points, score.gole_points, score.total_points), (1, 1, 2))


class CoreHardeningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="hardening", password="secret")
        self.round = Round.objects.create(name="Current", is_active=True)
        self.client.force_login(self.user)

    def match(self, number=1, round_=None, kickoff=None):
        return Match.objects.create(
            round=round_ if round_ is not None else self.round, league="League",
            home_team=f"Home {number}", away_team=f"Away {number}",
            kickoff=kickoff or timezone.now() + timedelta(days=1),
        )

    def test_only_one_active_round_and_complete_final_score_are_allowed(self):
        with self.assertRaises(ValidationError):
            Round.objects.create(name="Second", is_active=True)
        with self.assertRaises(ValidationError):
            Match.objects.create(
                round=self.round, league="League", home_team="Incomplete", away_team="Score",
                kickoff=timezone.now() + timedelta(days=1), home_goals=1,
            )

    def test_result_correction_recalculates_authoritative_score(self):
        match = self.match()
        Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        with self.captureOnCommitCallbacks(execute=True):
            match.home_goals, match.away_goals = 1, 0
            match.save()
        self.assertEqual(self.user.round_scores.get(round=self.round).typy_points, 1)
        with self.captureOnCommitCallbacks(execute=True):
            match.home_goals, match.away_goals = 0, 1
            match.save()
        score = self.user.round_scores.get(round=self.round)
        self.assertEqual((score.typy_points, score.total_points), (0, 0))
        recalculate_user_round_score(self.user, self.round)
        score.refresh_from_db()
        self.assertEqual((score.typy_points, score.total_points), (0, 0))

    def test_cross_column_draft_candidate_reuse_is_rejected(self):
        draft = Draft.objects.create(name="Draft", starts_at=timezone.now())
        first, second, third = [
            Match.objects.create(league="League", home_team=f"Draft Home {number}", away_team=f"Draft Away {number}", kickoff=timezone.now() + timedelta(days=1))
            for number in range(1, 4)
        ]
        DraftPair.objects.create(draft=draft, day_number=1, match_a=first, match_b=second)
        with self.assertRaises(ValidationError):
            DraftPair.objects.create(draft=draft, day_number=1, match_a=third, match_b=first)

    def test_new_chip_after_kickoff_is_rejected_by_domain_model(self):
        match = self.match(kickoff=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=match)

    def test_saved_change_mind_keeps_only_standard_prediction_editable_for_60_minutes(self):
        match = self.match(55)
        ChipAssignment.objects.create(user=self.user, round=self.round, chip="CHANGE_MIND", match=match)
        Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        match.kickoff = timezone.now() - timedelta(minutes=59)
        match.save(update_fields=["kickoff"])
        response = self.client.post(reverse("typy"), {
            f"result_{match.id}": "2", "confirm_less_than_ten": "1",
        })
        self.assertRedirects(response, reverse("typy"))
        self.assertEqual(Prediction.objects.get(user=self.user, match=match).predicted_result, "2")

    def test_old_round_goals_do_not_consume_current_round_goal_limit(self):
        old_round = Round.objects.create(name="Old")
        for number in range(10):
            old_match = self.match(number + 10, round_=old_round)
            Prediction.objects.create(user=self.user, match=old_match, predicted_result="1", total_goals=2)
        current = self.match(99)
        response = self.client.post(reverse("typy"), {
            f"result_{current.id}": "1", f"goals_{current.id}": "2", "confirm_less_than_ten": "1",
        })
        self.assertRedirects(response, reverse("typy"))

    def test_failed_save_rolls_back_chips_and_predictions_together(self):
        match = self.match()
        payload = {f"result_{match.id}": "1", f"chip_{match.id}": "BANKER", "confirm_less_than_ten": "1"}
        with patch("matches.views.Prediction.objects.update_or_create", side_effect=DatabaseError("write failed")):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse("typy"), payload)
        self.assertFalse(ChipAssignment.objects.filter(user=self.user, round=self.round).exists())
        self.assertFalse(Prediction.objects.filter(user=self.user, match=match).exists())

    def test_removing_swap_removes_the_replacement_prediction_from_current_state(self):
        original = self.match(200)
        replacement = Match.objects.create(
            league="League", home_team="Replacement Home", away_team="Replacement Away",
            kickoff=timezone.now() + timedelta(days=1),
        )
        assignment = ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=original)
        ChipAssignment.objects.filter(pk=assignment.pk).update(chip="SWAP", replacement_match=replacement)
        Prediction.objects.create(user=self.user, match=replacement, predicted_result="1", total_goals=3)

        response = self.client.post(reverse("typy"), {
            f"result_{original.id}": "1", "confirm_less_than_ten": "1",
        })
        self.assertRedirects(response, reverse("typy"))
        self.assertFalse(Prediction.objects.filter(user=self.user, match=replacement).exists())
        self.assertFalse(ChipAssignment.objects.filter(user=self.user, round=self.round).exists())


class PlayerStatisticsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="statistics", password="secret")
        self.round = Round.objects.create(name="Statistics")

    def create_finished_match(self, number, home, away):
        match = Match.objects.create(
            round=self.round, league="League", home_team=f"Home {number}", away_team=f"Away {number}",
            kickoff=timezone.now() - timedelta(days=1),
        )
        match.home_goals, match.away_goals = home, away
        match.save()
        return match

    def test_counts_only_submitted_predictions_on_finished_matches(self):
        correct = self.create_finished_match(1, 2, 1)
        wrong = self.create_finished_match(2, 0, 1)
        unpredicted = self.create_finished_match(3, 1, 1)
        live = Match.objects.create(round=self.round, league="League", home_team="Live Home", away_team="Live Away", kickoff=timezone.now() - timedelta(minutes=1))
        Prediction.objects.create(user=self.user, match=correct, predicted_result="1", total_goals=3)
        Prediction.objects.create(user=self.user, match=wrong, predicted_result="1", total_goals=2)
        Prediction.objects.create(user=self.user, match=live, predicted_result="1", total_goals=1)

        statistics = calculate_player_statistics(self.user)
        self.assertEqual((statistics.typy.submitted, statistics.typy.correct, statistics.typy.incorrect, statistics.typy.accuracy), (2, 1, 1, 50.0))
        self.assertEqual((statistics.gole.submitted, statistics.gole.correct, statistics.gole.incorrect, statistics.gole.accuracy), (2, 1, 1, 50.0))
        self.assertEqual(unpredicted.status, Match.Status.FINISHED)

    def test_returns_none_accuracy_when_no_qualifying_predictions_exist(self):
        self.create_finished_match(1, 1, 0)
        statistics = calculate_player_statistics(self.user)
        self.assertEqual(statistics.typy.accuracy, None)
        self.assertEqual(statistics.gole.accuracy, None)

    def test_swap_statistics_use_the_effective_replacement_match(self):
        original = Match.objects.create(
            round=self.round, league="League", home_team="Home 1", away_team="Away 1",
            kickoff=timezone.now() + timedelta(days=1),
        )
        replacement = Match.objects.create(
            league="League", home_team="Replacement Home", away_team="Replacement Away",
            kickoff=timezone.now() - timedelta(days=1),
        )
        replacement.home_goals, replacement.away_goals = 1, 2
        replacement.save()
        assignment = ChipAssignment.objects.create(user=self.user, round=self.round, chip="BANKER", match=original)
        ChipAssignment.objects.filter(pk=assignment.pk).update(chip="SWAP", replacement_match=replacement)
        original.home_goals, original.away_goals = 0, 0
        original.save()
        Prediction.objects.create(user=self.user, match=replacement, predicted_result="2", total_goals=3)

        statistics = calculate_player_statistics(self.user)
        self.assertEqual((statistics.typy.correct, statistics.gole.correct), (1, 1))


class RankingTests(TestCase):
    def setUp(self):
        self.season = Match50Season.objects.create(name="MATCH50 Test", starts_at=timezone.localdate().replace(month=1, day=1), ends_at=timezone.localdate().replace(month=12, day=31), is_active=True)
        self.round = Round.objects.create(name="Ranking", ranking_date=timezone.localdate(), match50_season=self.season)

    def score(self, username, typy, gole, bonusy, round_=None):
        user = get_user_model().objects.create(username=username)
        UserRoundScore.objects.create(user=user, round=round_ or self.round, typy_points=typy, gole_points=gole, bonus_points=bonusy)
        return user

    def test_competition_ranking_orders_and_keeps_exact_ties(self):
        self.score("first", 5, 1, 4)
        self.score("tied_a", 3, 2, 2)
        self.score("tied_b", 3, 2, 2)
        self.score("fourth", 1, 1, 4)
        entries = round_ranking(self.round)
        self.assertEqual([(entry.username, entry.rank) for entry in entries], [("first", 1), ("tied_a", 2), ("tied_b", 2), ("fourth", 4)])

    def test_month_and_season_aggregate_persisted_round_scores(self):
        user = self.score("aggregate", 2, 1, -1)
        second = Round.objects.create(name="Second", ranking_date=timezone.localdate(), match50_season=self.season)
        UserRoundScore.objects.create(user=user, round=second, typy_points=3, gole_points=2, bonus_points=1)
        other_season = Match50Season.objects.create(name="Other", starts_at=timezone.localdate().replace(year=timezone.localdate().year-1, month=1, day=1), ends_at=timezone.localdate().replace(year=timezone.localdate().year-1, month=12, day=31))
        outside = Round.objects.create(name="Outside", ranking_date=timezone.localdate().replace(year=timezone.localdate().year-1), match50_season=other_season)
        UserRoundScore.objects.create(user=user, round=outside, typy_points=99, gole_points=0, bonus_points=0)
        monthly = month_ranking(self.round.ranking_date.year, self.round.ranking_date.month)[0]
        seasonal = season_ranking(self.season)[0]
        self.assertEqual((monthly.typy, monthly.gole, monthly.bonusy, monthly.total), (5, 3, 0, 8))
        self.assertEqual((seasonal.typy, seasonal.gole, seasonal.bonusy, seasonal.total), (5, 3, 0, 8))

    def test_top_limit_keeps_current_user_true_position(self):
        users = [self.score(f"rank{number}", 101-number, 0, 0) for number in range(101)]
        entries = round_ranking(self.round)
        top, current = top_with_current(entries, users[-1])
        self.assertEqual(len(top), 100)
        self.assertEqual((current.username, current.rank), ("rank100", 101))

    def test_profile_current_performance_uses_existing_ranking_sources(self):
        self.round.is_active = True
        self.round.save(update_fields=["is_active"])
        player = self.score("performance", 4, 2, 1)
        self.score("round_leader", 8, 1, 1)
        completed = Round.objects.create(name="Completed", ranking_date=timezone.localdate(), match50_season=self.season, match_count=1)
        Match.objects.create(round=completed, league="L", home_team="A", away_team="B", kickoff=timezone.now()-timedelta(days=1), home_goals=1, away_goals=0)
        UserRoundScore.objects.create(user=player, round=completed, typy_points=10, gole_points=2, total_points=12)

        performance = current_performance(player)

        self.assertEqual(performance["round"], {"points": 7, "rank": 2})
        self.assertEqual(performance["month"], {"points": 12, "rank": 1})
        self.assertEqual(performance["season"], {"points": 12, "rank": 1})


class FinalStageSixTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username="stage6",password="secret")
        self.round=Round.objects.create(name="Stage 6",is_active=True)

    def test_early_bird_is_unique(self):
        self.client.force_login(self.user)
        match=Match.objects.create(round=self.round,league="L",home_team="A",away_team="B",kickoff=timezone.now()+timedelta(days=1))
        response=self.client.post(reverse("typy"),{f"result_{match.id}":"1","confirm_less_than_ten":"1"})
        self.assertRedirects(response,reverse("typy")); self.round.refresh_from_db()
        self.assertEqual(self.round.early_bird_user,self.user)

    def test_snapshot_lone_wolf_and_david_use_persisted_counts(self):
        match=Match.objects.create(round=self.round,league="L",home_team="A",away_team="B",kickoff=timezone.now()-timedelta(days=1),home_goals=1,away_goals=0)
        Prediction.objects.create(user=self.user,match=match,predicted_result="1")
        UserRoundScore.objects.create(user=self.user,round=self.round,typy_points=1,breakdown=[{"effective_match":match.id,"typy":1,"standard_prediction":["1"]}])
        MatchKickoffSnapshot.objects.create(match=match,counts={"1":1,"X":100,"2":100})
        evaluate_snapshot_achievements(self.user)
        self.assertTrue(UserAchievement.objects.filter(user=self.user,achievement__code="LONE_WOLF").exists())
        self.assertTrue(UserAchievement.objects.filter(user=self.user,achievement__code=f"DAVID_{match.id}").exists())

    def test_photo_finish_unlocks_for_tied_leaders(self):
        self.round.match_count = 1
        self.round.save(update_fields=["match_count"])
        Match.objects.create(round=self.round, league="L", home_team="A", away_team="B", kickoff=timezone.now()-timedelta(days=1), home_goals=1, away_goals=0)
        other=get_user_model().objects.create_user(username="tie",password="secret")
        UserRoundScore.objects.create(user=self.user,round=self.round,typy_points=2)
        UserRoundScore.objects.create(user=other,round=self.round,typy_points=2)
        evaluate_trophies(self.round)
        self.assertEqual(UserAchievement.objects.filter(achievement__code="PHOTO_FINISH").count(),2)

    def test_profile_progress_uses_actual_typy_value_between_tiers(self):
        from .services.achievements import _tiers, TIERS
        _tiers(self.user, "TYPY", "TYPY", 18, TIERS, {})
        progress = profile_data(self.user)["paths"]["typy"]
        self.assertEqual(progress["progress"], 18)
        self.assertEqual(progress["next"], ("PLATINUM", 22))
        self.assertEqual(progress["percent"], 82)

    @patch("matches.services.achievements.typy_streaks", return_value={"current_streak": 7, "max_streak": 7})
    def test_profile_progress_uses_actual_streak_between_tiers(self, _streaks):
        from .services.achievements import evaluate_score
        evaluate_score(UserRoundScore.objects.create(user=self.user, round=self.round))
        progress = profile_data(self.user)["paths"]["streak"]
        self.assertEqual(progress["progress"], 7)
        self.assertEqual(progress["next"], ("SILVER", 8))
        self.assertEqual(progress["percent"], 88)

    def test_profile_mastery_uses_actual_value_between_tiers_and_keeps_all_paths(self):
        competition = Competition.objects.create(code="EPL", name="Premier League", country="England")
        team = Team.objects.create(name="Arsenal")
        season = CompetitionSeason.objects.create(competition=competition, season_label="2026/27", champion_team=team)
        match = Match.objects.create(round=self.round, league="Premier League", home_team="Arsenal", away_team="Chelsea", kickoff=timezone.now()-timedelta(days=1), home_goals=1, away_goals=0, competition_season=season, home_team_entity=team)
        UserRoundScore.objects.create(user=self.user, round=self.round, breakdown=[{"effective_match": match.id, "typy": 1}] * 18)
        from .services.achievements import evaluate_user
        evaluate_user(self.user)
        data = profile_data(self.user)
        mastery = next(item["data"] for item in data["mastery"] if item["code"] == "EPL")
        self.assertEqual((mastery["progress"], mastery["next"], mastery["percent"]), (18, ("SILVER", 25), 72))
        self.assertEqual(len(data["mastery"]), 9)
        self.assertEqual(
            [item["code"] for item in data["mastery"]],
            ["EPL", "BUNDESLIGA", "UCL", "UECL", "EKSTRAKLASA", "UEL", "LALIGA", "LIGUE1", "SERIEA"],
        )

    def test_history_is_round_centric_and_keeps_unpredicted_slots_neutral(self):
        matches = [
            Match.objects.create(round=self.round, league="League", home_team=f"Home {number}", away_team=f"Away {number}", kickoff=timezone.now()-timedelta(days=2, minutes=number), home_goals=1, away_goals=0)
            for number in range(30)
        ]
        for match in matches[:27]:
            Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        UserRoundScore.objects.create(user=self.user, round=self.round, typy_points=27, breakdown=[{"match": match.id, "effective_match": match.id, "typy": 1, "standard_correct": True} for match in matches[:27]])
        history, _ = public_history(self.user)
        group = history.object_list[0]
        self.assertEqual(len(group["slots"]), 30)
        self.assertEqual(sum(not slot.saved_prediction for slot in group["slots"]), 3)
        self.assertEqual(sum(slot.standard_score_state == "miss" for slot in group["slots"]), 0)
        self.assertTrue(all(slot.effective_match.kickoff for slot in group["slots"]))
        self.assertEqual(calculate_player_statistics(self.user).typy.submitted, 27)
        response = self.client.get(reverse("player_profile", args=[self.user.username]))
        self.assertNotContains(response, matches[0].kickoff.strftime("%d.%m.%Y"))
        history_response = self.client.get(reverse("player_round_history", args=[self.user.username]))
        self.assertContains(history_response, matches[0].kickoff.strftime("%d.%m.%Y"))

    def test_history_competition_filter_shows_only_matching_matches(self):
        epl = Competition.objects.create(code="EPL", name="Premier League", country="England")
        laliga = Competition.objects.create(code="LALIGA", name="La Liga", country="Spain")
        arsenal = Team.objects.create(name="Filter Arsenal")
        barcelona = Team.objects.create(name="Filter Barcelona")
        epl_season = CompetitionSeason.objects.create(competition=epl, season_label="2026/27", champion_team=arsenal)
        laliga_season = CompetitionSeason.objects.create(competition=laliga, season_label="2026/27", champion_team=barcelona)
        premier_match = Match.objects.create(round=self.round, league="Premier League", home_team="Filter Arsenal", away_team="Filter Chelsea", kickoff=timezone.now()-timedelta(days=2), home_goals=1, away_goals=0, competition_season=epl_season, home_team_entity=arsenal)
        Match.objects.create(round=self.round, league="La Liga", home_team="Filter Barcelona", away_team="Filter Madrid", kickoff=timezone.now()-timedelta(days=2, minutes=1), home_goals=1, away_goals=0, competition_season=laliga_season, home_team_entity=barcelona)

        history, _ = public_history(self.user, competition="EPL")

        self.assertEqual(len(history.object_list), 1)
        self.assertEqual([slot.effective_match.id for slot in history.object_list[0]["slots"]], [premier_match.id])

    def test_history_round_filter_preserves_complete_selected_round(self):
        first = Match.objects.create(round=self.round, league="League", home_team="A", away_team="B", kickoff=timezone.now()-timedelta(days=2), home_goals=1, away_goals=0)
        Prediction.objects.create(user=self.user, match=first, predicted_result="1")
        UserRoundScore.objects.create(user=self.user, round=self.round, breakdown=[{"match": first.id, "effective_match": first.id, "typy": 1, "standard_correct": True}])
        other = Round.objects.create(name="Older completed round")
        Match.objects.create(round=other, league="League", home_team="C", away_team="D", kickoff=timezone.now()-timedelta(days=3), home_goals=1, away_goals=0)
        history, _ = public_history(self.user, round_id=self.round.id)
        self.assertEqual(history.paginator.count, 1)
        self.assertEqual(history.object_list[0]["round"], self.round)

    def test_profile_round_history_preview_is_limited_to_five_recent_rounds(self):
        rounds = []
        for number in range(6):
            round_ = Round.objects.create(
                name=f"Historia {number}",
                ranking_date=timezone.localdate() - timedelta(days=number),
                match_count=1,
            )
            Match.objects.create(
                round=round_, league="League", home_team=f"H{number}", away_team=f"A{number}",
                kickoff=timezone.now() - timedelta(days=number + 1), home_goals=1, away_goals=0,
            )
            UserRoundScore.objects.create(user=self.user, round=round_, typy_points=number + 1)
            rounds.append(round_)

        response = self.client.get(reverse("player_profile", args=[self.user.username]))

        preview = response.context["round_history_preview"]
        self.assertEqual([item["round"] for item in preview], rounds[:5])
        self.assertContains(response, "ZOBACZ CAŁĄ HISTORIĘ")
        self.assertNotContains(response, "Historia 5")

    def test_round_history_uses_classified_players_for_top_percent_and_selects_round(self):
        selected = Round.objects.create(name="Wybrana kolejka", ranking_date=timezone.localdate(), match_count=1)
        match = Match.objects.create(
            round=selected, league="League", home_team="Marker Home", away_team="Marker Away",
            kickoff=timezone.now() - timedelta(days=2), home_goals=2, away_goals=0,
        )
        UserRoundScore.objects.create(user=self.user, round=selected, typy_points=6)
        for index, points in enumerate((7, 5), start=1):
            rival = get_user_model().objects.create_user(username=f"classified{index}")
            UserRoundScore.objects.create(user=rival, round=selected, typy_points=points)
        Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        TrophyFinish.objects.create(user=self.user, scope="ROUND", period_key=str(selected.id), rank=2)

        profile_response = self.client.get(reverse("player_profile", args=[self.user.username]))
        history_url = reverse("player_round_history", args=[self.user.username])
        self.assertContains(profile_response, "6 PKT")
        self.assertContains(profile_response, "🥈")
        self.assertNotContains(profile_response, "TOP 67%")
        self.assertContains(profile_response, f'{history_url}?round={selected.id}')
        self.assertNotContains(profile_response, "Marker Home")

        history_response = self.client.get(history_url, {"round": selected.id})
        self.assertEqual(history_response.context["selected_summary"]["round"], selected)
        self.assertContains(history_response, "Wybrana kolejka")
        self.assertContains(history_response, "Marker Home")
        self.assertContains(history_response, "TOP 67%")

    def test_round_history_summary_maps_each_finalized_finish_to_one_status(self):
        expected = {1: "🥇", 2: "🥈", 3: "🥉", 4: "TOP 100%"}
        for position in range(1, 5):
            round_ = Round.objects.create(
                name=f"Podium {position}",
                ranking_date=timezone.localdate() - timedelta(days=position),
                match_count=1,
            )
            Match.objects.create(
                round=round_, league="League", home_team=f"P{position}", away_team="Away",
                kickoff=timezone.now() - timedelta(days=position), home_goals=1, away_goals=0,
            )
            UserRoundScore.objects.create(user=self.user, round=round_, typy_points=1)
            for rival_number in range(position - 1):
                rival = get_user_model().objects.create_user(username=f"podium-{position}-{rival_number}")
                UserRoundScore.objects.create(user=rival, round=round_, typy_points=2)
            if position <= 3:
                TrophyFinish.objects.create(
                    user=self.user, scope="ROUND", period_key=str(round_.id), rank=position,
                )

        summaries = {item["round"].name: item for item in round_history_summaries(self.user)}

        for position, status in expected.items():
            item = summaries[f"Podium {position}"]
            self.assertEqual(item["result_status"], status)
            self.assertEqual(item["medal"], status if position <= 3 else "")
            if position <= 3:
                self.assertNotIn("TOP", item["result_status"])

    def test_round_history_empty_state(self):
        empty_player = get_user_model().objects.create_user(username="empty-history")

        response = self.client.get(reverse("player_round_history", args=[empty_player.username]))

        self.assertContains(response, "Nie masz jeszcze kolejek do wyświetlenia.")
        self.assertIsNone(response.context["selected_summary"])

    def test_current_round_winner_resolves_its_origin_draft_loser(self):
        winner = Match.objects.create(round=self.round, league="L", home_team="Winner", away_team="Home", kickoff=timezone.now()+timedelta(days=2))
        loser = Match.objects.create(league="L", home_team="Loser", away_team="Away", kickoff=timezone.now()+timedelta(days=3))
        Round.objects.filter(pk=self.round.pk).update(is_active=False)
        origin = Draft.objects.create(name="Origin", starts_at=timezone.now()-timedelta(days=8), is_active=False)
        pair = DraftPair.objects.create(draft=origin, day_number=1, match_a=winner, match_b=loser, winner=winner, resolution_method="VOTE", resolved_at=timezone.now())
        Round.objects.filter(pk=self.round.pk).update(is_active=True)
        self.assertEqual(draft_loser_for_winner(winner), loser)
        self.assertEqual(pair.winner, winner)

    def test_resolved_future_winner_is_editable_and_has_its_draft_loser(self):
        candidate_a = Match.objects.create(league="L", home_team="Future A", away_team="Future B", kickoff=timezone.now()+timedelta(days=8))
        candidate_b = Match.objects.create(league="L", home_team="Future C", away_team="Future D", kickoff=timezone.now()+timedelta(days=9))
        draft = Draft.objects.create(name="Future", starts_at=timezone.now()-timedelta(days=2))
        pair = DraftPair.objects.create(draft=draft, day_number=1, match_a=candidate_a, match_b=candidate_b)
        pair.resolve()
        draft.resolve_closed_pairs()
        future = draft.next_round
        winner = pair.winner
        self.assertEqual(future.matches.count(), 1)
        self.assertEqual(draft_loser_for_winner(winner), candidate_b if winner == candidate_a else candidate_a)

    def test_new_result_goals_and_banker_save_together(self):
        self.client.force_login(self.user)
        match = Match.objects.create(round=self.round, league="L", home_team="A", away_team="B", kickoff=timezone.now()+timedelta(days=2))
        response = self.client.post(reverse("typy"), {f"result_{match.id}": "1", f"goals_{match.id}": "3", f"chip_{match.id}": "BANKER", "confirm_less_than_ten": "1"})
        self.assertRedirects(response, reverse("typy"))
        self.assertEqual(Prediction.objects.get(user=self.user, match=match).total_goals, 3)
        self.assertTrue(ChipAssignment.objects.filter(user=self.user, round=self.round, match=match, chip="BANKER").exists())

    def test_goal_without_any_standard_prediction_is_saved(self):
        self.client.force_login(self.user)
        match = Match.objects.create(round=self.round, league="L", home_team="A", away_team="B", kickoff=timezone.now()+timedelta(days=2))
        response = self.client.post(reverse("typy"), {f"goals_{match.id}": "3", "confirm_less_than_ten": "1"})
        self.assertRedirects(response, reverse("typy"))
        prediction = Prediction.objects.get(user=self.user, match=match)
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("", 3))

    def test_ranking_username_links_to_the_shared_public_profile(self):
        previous = Round.objects.create(name="Completed ranking", match_count=1)
        Match.objects.create(round=previous, league="L", home_team="A", away_team="B", kickoff=timezone.now()-timedelta(days=1), home_goals=1, away_goals=0)
        UserRoundScore.objects.create(user=self.user, round=previous, typy_points=3)
        response = self.client.get(reverse("rankings"))
        self.assertContains(response, reverse("player_profile", args=[self.user.username]))

    def test_unfinished_round_score_cannot_contaminate_month_or_season_ranking(self):
        Match50Season.objects.update(is_active=False)
        season = Match50Season.objects.create(name="Ranking scope", starts_at=timezone.localdate()-timedelta(days=2), ends_at=timezone.localdate()+timedelta(days=2), is_active=True)
        self.round.match50_season = season
        self.round.save(update_fields=["match50_season"])
        UserRoundScore.objects.create(user=self.user, round=self.round, typy_points=99, gole_points=99)
        completed = Round.objects.create(name="Completed", ranking_date=timezone.localdate(), match50_season=season, match_count=1)
        Match.objects.create(round=completed, league="L", home_team="A", away_team="B", kickoff=timezone.now()-timedelta(days=1), home_goals=1, away_goals=0)
        UserRoundScore.objects.create(user=self.user, round=completed, typy_points=1, gole_points=1)
        self.assertEqual(month_ranking(completed.ranking_date.year, completed.ranking_date.month)[0].total, 2)
        self.assertEqual(season_ranking(season)[0].total, 2)


class CanonicalHistorySeedCleanupTests(TestCase):
    def test_cleanup_removes_a_development_owned_orphan_prediction_with_history(self):
        user = get_user_model().objects.create_user(username="demo_rank_cleanup")
        history = Round.objects.create(name="DEV PROFILE HISTORY CLEANUP", match_count=1)
        winner = Match.objects.create(
            round=history, league="Dev", home_team="Winner", away_team="Winner Away",
            kickoff=timezone.now() - timedelta(days=2), home_goals=1, away_goals=0,
        )
        loser = Match.objects.create(
            league="Dev", home_team="Loser", away_team="Loser Away",
            kickoff=timezone.now() - timedelta(days=2), home_goals=0, away_goals=1,
        )
        origin = Draft.objects.create(
            name="DEV HISTORY ORIGIN CLEANUP", starts_at=timezone.now() - timedelta(days=10),
            is_active=False, next_round_name=history.name,
        )
        pair = DraftPair.objects.create(draft=origin, day_number=1, match_a=winner, match_b=loser)
        pair.winner = winner
        pair.resolution_method = DraftPair.Resolution.VOTE
        pair.resolved_at = timezone.now()
        pair.save(update_fields=["winner", "resolution_method", "resolved_at"])
        Prediction.objects.create(user=user, match=winner, predicted_result="1")
        # This extra prediction is deliberately outside the Round, but its
        # paired loser makes it unambiguously owned by canonical history.
        Prediction.objects.create(user=user, match=loser, predicted_result="2", total_goals=4)

        SeedDevDataCommand()._clear_canonical_history()

        self.assertFalse(Round.objects.filter(pk=history.pk).exists())
        self.assertFalse(Draft.objects.filter(pk=origin.pk).exists())
        self.assertFalse(Match.objects.filter(pk=loser.pk).exists())
        self.assertFalse(Prediction.objects.filter(user=user).exists())
