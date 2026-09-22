from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ChipAssignment, Draft, DraftPair, Match, Prediction, Round, Team


class PredictionCardTests(TestCase):
    def test_team_shirts_use_canonical_team_configuration_and_neutral_fallback(self):
        configured = Team.objects.create(
            name="Striped FC", shirt_primary="#112233", shirt_secondary="#ddeeff",
            shirt_pattern=Team.ShirtPattern.VERTICAL_STRIPES,
        )
        self.a.home_team_entity = configured
        self.a.save(update_fields=["home_team_entity"])

        response = self.client.get(reverse("typy"))
        slot = response.context["prediction_ui"]["slots"][str(self.a.id)]["original"]
        self.assertEqual(slot["homeShirt"], {
            "primary": "#112233", "secondary": "#ddeeff", "pattern": "VERTICAL_STRIPES",
        })
        self.assertEqual(slot["awayShirt"]["pattern"], "SOLID")
        self.assertContains(response, 'data-shirt-pattern="VERTICAL_STRIPES"')
        self.assertContains(response, 'aria-label="Koszulka: Away"')

    def test_centralized_flags_use_football_countries_and_escape_unknown_names(self):
        from .services.league_flags import league_label
        for league, country in [('Premier League', 'England'), ('La Liga', 'Spain'), ('Serie A', 'Italy'),
                                ('Bundesliga', 'Germany'), ('Ligue 1', 'France'), ('Ekstraklasa', 'Poland')]:
            with self.subTest(league=league):
                label = str(league_label(league))
                self.assertIn(f'aria-label="{country}"', label)
                self.assertIn(league, label)
                self.assertNotIn('United Kingdom', label)
        for league in ['Champions League', 'Europa League', 'Conference League', 'Unknown League']:
            self.assertEqual(str(league_label(league)), league)
        self.assertEqual(str(league_label('<script>')), '&lt;script&gt;')

    def test_kickoff_presentation_uses_active_timezone_and_flags_render_in_history(self):
        self.a.league = 'Premier League'; self.a.save()
        with timezone.override('Europe/Warsaw'):
            response = self.client.get(reverse('typy'))
            kickoff = response.context['prediction_ui']['slots'][str(self.a.id)]['original']['kickoff']
            self.assertEqual(kickoff, timezone.localtime(self.a.kickoff).strftime('%d.%m.%Y · %H:%M'))
            self.assertContains(response, 'aria-label="England"')
        previous = Round.objects.create(name='History flags', match_count=1)
        Match.objects.create(round=previous, league='La Liga', home_team='H', away_team='A', kickoff=timezone.now()-timedelta(days=2), home_goals=1, away_goals=0)
        response = self.client.get(reverse('typy') + '?tab=previous')
        self.assertContains(response, 'aria-label="Spain"')

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cards")
        self.client.force_login(self.user)
        self.round = Round.objects.create(name="Cards", is_active=True)
        self.a = self.match("A")
        self.b = self.match("B")

    def match(self, name, round_=None):
        return Match.objects.create(round=round_ or self.round, league="League", home_team=name,
                                    away_team="Away", kickoff=timezone.now() + timedelta(days=3))

    def save_card(self, match=None, result="1", chip="", goals="", **extra):
        match = match or self.a
        payload = {"card_match": match.id, "round_id": match.round_id, "ui_save": "1",
                   "confirm_less_than_ten": "1", f"result_{match.id}": result,
                   f"chip_{match.id}": chip, f"goals_{match.id}": goals}
        if result == "":
            payload.pop(f"result_{match.id}")
        payload.update(extra)
        url = reverse("typy") + ("?tab=future" if match.round_id != self.round.id else "")
        return self.client.post(url, payload)

    def save_list(self, *, result=None, chip="", goals=None, swap_change=False, **extra):
        payload = {
            "ui_save": "1", "round_id": self.round.pk,
            "confirm_less_than_ten": "1",
            f"chip_{self.a.pk}": chip,
            f"chip_{self.b.pk}": "",
        }
        if result is not None:
            payload[f"result_{self.a.pk}"] = result
        if goals is not None:
            payload[f"goals_{self.a.pk}"] = goals
        if swap_change:
            payload["swap_change"] = str(self.a.pk)
        payload.update(extra)
        return self.client.post(reverse("typy"), payload)

    def test_current_card_preserves_other_prediction_and_chip(self):
        prediction = Prediction.objects.create(user=self.user, match=self.b, predicted_result="2", total_goals=4)
        assignment = ChipAssignment.objects.create(user=self.user, round=self.round, match=self.b, chip="BANKER")
        response = self.save_card(goals="3", **{f"result_{self.b.id}": "X", f"chip_{self.b.id}": ""})
        self.assertTrue(response.json()["saved"])
        self.assertEqual(Prediction.objects.get(user=self.user, match=self.a).total_goals, 3)
        prediction.refresh_from_db(); assignment.refresh_from_db()
        self.assertEqual((prediction.predicted_result, prediction.total_goals, assignment.chip), ("2", 4, "BANKER"))

    def test_partial_future_exposes_only_existing_slots_and_saves_in_place(self):
        future = Round.objects.create(name="Future")
        draft = Draft.objects.create(name="Draft", starts_at=timezone.now(), next_round=future)
        match = self.match("Future A", future)
        response = self.client.get(reverse("typy") + "?tab=future")
        self.assertEqual(list(response.context["prediction_ui"]["slots"]), [str(match.id)])
        self.assertEqual(response.context["prediction_ui"]["total"], 30)
        self.assertTrue(self.save_card(match, "2").json()["saved"])
        self.assertEqual(Prediction.objects.get(match=match).predicted_result, "2")
        self.assertEqual(future.matches.count(), 1)
        draft.refresh_from_db(); self.assertEqual(draft.next_round_id, future.id)
        self.assertEqual(self.round.matches.count(), 2)
        Round.objects.filter(pk=self.round.pk).update(is_active=False)
        Round.objects.filter(pk=future.pk).update(is_active=True)
        response = self.client.get(reverse("typy"))
        self.assertEqual(response.context["matches"][0].saved_prediction, "2")
        self.assertEqual(Prediction.objects.count(), 1)

    def test_unknown_card_and_stale_round_are_rejected(self):
        self.assertEqual(self.save_card(card_match=999999).status_code, 400)
        self.assertEqual(self.save_card(round_id=999999).status_code, 400)
        self.assertFalse(Prediction.objects.exists())

    def test_double_pick_requires_exactly_two_and_can_be_removed(self):
        self.assertEqual(self.save_card(result=["1"], chip="DOUBLE_PICK").status_code, 400)
        self.assertFalse(Prediction.objects.exists())
        self.assertTrue(self.save_card(result=["1", "X"], chip="DOUBLE_PICK").json()["saved"])
        self.assertEqual(ChipAssignment.objects.get().outcomes, ["1", "X"])
        self.assertTrue(self.save_card(result="1").json()["saved"])
        self.assertFalse(ChipAssignment.objects.exists())

    def test_goal_chip_rejects_draw_and_saves_team(self):
        self.assertEqual(self.save_card(result="X", chip="GOOOOOOOOAL").status_code, 400)
        self.assertFalse(ChipAssignment.objects.exists())
        self.assertTrue(self.save_card(result="2", chip="GOOOOOOOOAL").json()["saved"])
        self.assertEqual(ChipAssignment.objects.get().goal_team, self.a.away_team)

    def test_used_chip_cannot_be_reused(self):
        self.save_card(chip="BANKER")
        response = self.save_card(self.b, chip="BANKER")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Prediction.objects.filter(match=self.b).exists())
        self.assertEqual(ChipAssignment.objects.get().match_id, self.a.id)

    def test_goals_limit_counts_untouched_cards(self):
        for number in range(10):
            Prediction.objects.create(user=self.user, match=self.match(str(number)), predicted_result="1", total_goals=2)
        self.assertEqual(self.save_card(goals="3").status_code, 400)
        self.assertEqual(Prediction.objects.count(), 10)

    def test_goals_may_be_saved_without_standard_prediction(self):
        response = self.save_card(result="", goals="3")
        self.assertTrue(response.json()["saved"])
        prediction = Prediction.objects.get(user=self.user, match=self.a)
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("", 3))
        slot = response.json()["slots"][str(self.a.id)]
        self.assertEqual(slot["outcomes"], [])
        self.assertFalse(slot["predicted"])
        self.assertEqual(slot["goals"], 3)

    def test_standard_prediction_may_be_cleared_while_goals_remain_saved(self):
        Prediction.objects.create(user=self.user, match=self.a, predicted_result="1", total_goals=3)
        response = self.save_card(result="", goals="3", clear_result=str(self.a.pk))
        self.assertTrue(response.json()["saved"])
        prediction = Prediction.objects.get(user=self.user, match=self.a)
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("", 3))
        self.assertFalse(response.json()["completion"]["typyComplete"])
        self.assertEqual(response.json()["completion"]["goalsSelected"], 1)

    def test_goal_only_state_does_not_satisfy_banker_prediction_requirement(self):
        Prediction.objects.create(user=self.user, match=self.a, predicted_result="", total_goals=3)
        response = self.save_card(result="", goals="3", chip="BANKER")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ChipAssignment.objects.exists())

    def test_response_reflects_existing_goal_only_fallback(self):
        Prediction.objects.create(user=self.user, match=self.a, predicted_result="X", total_goals=2)
        response = self.save_card(result="", goals="3")
        self.assertEqual(response.json()["slots"][str(self.a.id)]["outcomes"], ["X"])
        self.assertEqual(response.json()["slots"][str(self.a.id)]["goals"], 3)

    def swap_pair(self):
        loser = self.match("Loser")
        loser.round = None; loser.save()
        Round.objects.filter(pk=self.round.pk).update(is_active=False)
        draft = Draft.objects.create(name="Origin", starts_at=timezone.now(), is_active=False)
        DraftPair.objects.create(draft=draft, day_number=1, match_a=self.a, match_b=loser,
                                 winner=self.a, resolution_method="VOTE", resolved_at=timezone.now())
        Round.objects.filter(pk=self.round.pk).update(is_active=True)
        return loser

    def test_swap_targets_loser_then_removal_cleans_replacement(self):
        loser = self.swap_pair()
        self.assertTrue(self.save_card(chip="SWAP", result="2", goals="4").json()["saved"])
        self.assertEqual(Prediction.objects.get(match=loser).predicted_result, "2")
        self.assertFalse(Prediction.objects.filter(match=self.a).exists())
        self.assertEqual(ChipAssignment.objects.get().replacement_match_id, loser.id)
        self.assertTrue(self.save_card(result="X").json()["saved"])
        self.assertFalse(Prediction.objects.filter(match=loser).exists())
        self.assertEqual(Prediction.objects.get(match=self.a).predicted_result, "X")

    def test_goal_limit_ignores_retained_swapped_out_prediction(self):
        loser = self.swap_pair()
        Prediction.objects.create(user=self.user, match=self.a, total_goals=3)
        ChipAssignment.objects.create(user=self.user, round=self.round, match=self.a,
                                      chip="SWAP", replacement_match=loser)
        for index in range(9):
            Prediction.objects.create(user=self.user, match=self.match(f"Goal {index}"), total_goals=2)

        response = self.save_card(match=self.b, goals="4")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        # An actual eleventh effective prediction must still be rejected.
        response = self.save_card(match=self.a, chip="SWAP", goals="2")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())

    def test_card_swap_transitions_apply_typ_and_gole_to_final_effective_match(self):
        loser = self.swap_pair()
        others = [self.match(f"Boundary {index}") for index in range(9)]
        for match in others:
            Prediction.objects.create(user=self.user, match=match, predicted_result="1", total_goals=1)

        # No SWAP -> No SWAP: A is the effective tenth GOLE selection.
        response = self.save_card(result="1", goals="2")
        self.assertTrue(response.json()["saved"])
        self.assertEqual(
            (Prediction.objects.get(match=self.a).predicted_result,
             Prediction.objects.get(match=self.a).total_goals),
            ("1", 2),
        )

        # No SWAP -> SWAP: changing the fixture preserves A, but does not count
        # it while B is effective. The following card save belongs to B.
        self.assertTrue(self.save_card(chip="SWAP", result="", chip_action="1").json()["saved"])
        response = self.save_card(chip="SWAP", result="2", goals="3")
        self.assertTrue(response.json()["saved"])
        self.assertEqual(
            (Prediction.objects.get(match=loser).predicted_result,
             Prediction.objects.get(match=loser).total_goals),
            ("2", 3),
        )
        self.assertEqual(Prediction.objects.filter(user=self.user, total_goals__isnull=False).count(), 11)
        self.assertEqual(response.json()["completion"]["goalsSelected"], 10)

        # SWAP -> SWAP: B remains the prediction target.
        response = self.save_card(chip="SWAP", result="X", goals="4")
        self.assertTrue(response.json()["saved"])
        self.assertEqual(
            (Prediction.objects.get(match=loser).predicted_result,
             Prediction.objects.get(match=loser).total_goals),
            ("X", 4),
        )

        # SWAP -> No SWAP: A becomes effective again and B is removed. The
        # retained A state is then editable through the ordinary card save.
        response = self.save_card(result="", chip_action="1")
        self.assertTrue(response.json()["saved"])
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())
        self.assertEqual(response.json()["completion"]["goalsSelected"], 10)
        response = self.save_card(result="2", goals="5")
        self.assertTrue(response.json()["saved"])
        original = Prediction.objects.get(user=self.user, match=self.a)
        self.assertEqual((original.predicted_result, original.total_goals), ("2", 5))

        # A genuinely independent eleventh GOLE remains rejected atomically.
        before = (original.predicted_result, original.total_goals)
        response = self.save_card(self.b, result="1", goals="6")
        self.assertEqual(response.status_code, 400)
        original.refresh_from_db()
        self.assertEqual((original.predicted_result, original.total_goals), before)
        self.assertFalse(Prediction.objects.filter(user=self.user, match=self.b).exists())

    def test_card_swap_goal_clears_do_not_resurrect_previous_effective_state(self):
        loser = self.swap_pair()
        Prediction.objects.create(user=self.user, match=self.a, predicted_result="1", total_goals=2)

        # A -> B with an explicitly empty GOLE leaves B empty.
        self.assertTrue(self.save_card(chip="SWAP", result="", chip_action="1").json()["saved"])
        self.assertTrue(self.save_card(chip="SWAP", result="2", goals="").json()["saved"])
        self.assertIsNone(Prediction.objects.get(user=self.user, match=loser).total_goals)

        # Give B a GOLE, return to A, then explicitly clear A. Neither action
        # may resurrect B after it ceases to be effective.
        self.assertTrue(self.save_card(chip="SWAP", result="2", goals="3").json()["saved"])
        self.assertTrue(self.save_card(result="", chip_action="1").json()["saved"])
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())
        response = self.save_card(result="1", goals="")
        self.assertTrue(response.json()["saved"])
        self.assertIsNone(Prediction.objects.get(user=self.user, match=self.a).total_goals)
        slot = response.json()["slots"][str(self.a.pk)]
        self.assertEqual((slot["outcomes"], slot["goals"]), (["1"], ""))

    def test_list_swap_transition_counts_only_submitted_effective_fixture(self):
        self.swap_pair()
        Prediction.objects.create(user=self.user, match=self.a, total_goals=3)
        others = [self.match(f"Goal {index}") for index in range(9)]
        payload = {f"goals_{match.pk}": "2" for match in others}
        response = self.save_list(chip="SWAP", goals="4", swap_change=True, **payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])

    def test_swap_action_without_prediction_makes_unpredicted_alternative_effective(self):
        loser = self.swap_pair()
        response = self.save_card(chip="SWAP", result="", chip_action="1")
        self.assertTrue(response.json()["saved"])
        assignment = ChipAssignment.objects.get(user=self.user, match=self.a)
        self.assertEqual(assignment.replacement_match_id, loser.id)
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())
        slot = response.json()["slots"][str(self.a.id)]
        self.assertEqual((slot["home"], slot["away"]), (loser.home_team, loser.away_team))
        self.assertEqual(slot["outcomes"], [])

    def test_list_save_applies_staged_swap_without_transferring_prediction(self):
        loser = self.swap_pair()
        original = Prediction.objects.create(
            user=self.user, match=self.a, predicted_result="1", total_goals=3
        )
        response = self.client.post(reverse("typy"), {
            "ui_save": "1", "round_id": self.round.pk,
            "swap_change": str(self.a.pk),
            f"chip_{self.a.pk}": "SWAP",
            f"chip_{self.b.pk}": "",
            "confirm_less_than_ten": "1",
        })
        self.assertTrue(response.json()["saved"])
        self.assertTrue(ChipAssignment.objects.filter(
            user=self.user, match=self.a, chip="SWAP", replacement_match=loser
        ).exists())
        self.assertTrue(Prediction.objects.filter(pk=original.pk, match=self.a).exists())
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())

    def test_list_no_swap_to_swap_persists_submitted_alternative_prediction(self):
        loser = self.swap_pair()
        response = self.save_list(result="2", chip="SWAP", swap_change=True)
        self.assertTrue(response.json()["saved"])
        self.assertTrue(ChipAssignment.objects.filter(
            user=self.user, match=self.a, chip="SWAP", replacement_match=loser
        ).exists())
        self.assertEqual(Prediction.objects.get(user=self.user, match=loser).predicted_result, "2")
        self.assertFalse(Prediction.objects.filter(user=self.user, match=self.a).exists())

    def test_list_swap_to_no_swap_updates_original_instead_of_restoring_stale_value(self):
        loser = self.swap_pair()
        original = Prediction.objects.create(user=self.user, match=self.a, predicted_result="1")
        ChipAssignment.objects.create(
            user=self.user, round=self.round, match=self.a, chip="SWAP", replacement_match=loser
        )
        Prediction.objects.create(user=self.user, match=loser, predicted_result="1")
        response = self.save_list(result="2", chip="", swap_change=True)
        self.assertTrue(response.json()["saved"])
        original.refresh_from_db()
        self.assertEqual(original.predicted_result, "2")
        self.assertFalse(ChipAssignment.objects.filter(user=self.user, match=self.a).exists())
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())

    def test_list_persisted_swap_prediction_updates_on_alternative(self):
        loser = self.swap_pair()
        ChipAssignment.objects.create(
            user=self.user, round=self.round, match=self.a, chip="SWAP", replacement_match=loser
        )
        prediction = Prediction.objects.create(user=self.user, match=loser, predicted_result="1")
        response = self.save_list(result="2", chip="SWAP")
        self.assertTrue(response.json()["saved"])
        prediction.refresh_from_db()
        self.assertEqual(prediction.predicted_result, "2")
        self.assertFalse(Prediction.objects.filter(user=self.user, match=self.a).exists())

    def test_list_normal_prediction_update_still_targets_original(self):
        prediction = Prediction.objects.create(user=self.user, match=self.a, predicted_result="1")
        response = self.save_list(result="2")
        self.assertTrue(response.json()["saved"])
        prediction.refresh_from_db()
        self.assertEqual(prediction.predicted_result, "2")

    def test_list_swap_transition_is_atomic_when_submitted_result_is_invalid(self):
        loser = self.swap_pair()
        response = self.save_list(result="9", chip="SWAP", swap_change=True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ChipAssignment.objects.filter(user=self.user, match=self.a).exists())
        self.assertFalse(Prediction.objects.filter(user=self.user, match__in=[self.a, loser]).exists())

    def test_effective_match_may_remain_unpredicted(self):
        self.swap_pair()
        self.assertTrue(self.save_card(chip="SWAP", result="", chip_action="1").json()["saved"])
        response = self.save_card(chip="SWAP", result="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Prediction.objects.exists())

    def test_explicit_clear_persists_29_of_30_and_clears_dependent_goals(self):
        matches = [self.a, self.b] + [self.match(str(i)) for i in range(28)]
        for match in matches:
            Prediction.objects.create(user=self.user, match=match, predicted_result="1")
        Prediction.objects.filter(match=self.a).update(total_goals=2)
        response = self.save_card(result="", clear_prediction=str(self.a.pk))
        self.assertTrue(response.json()["saved"])
        self.assertEqual(Prediction.objects.filter(user=self.user).count(), 29)
        self.assertFalse(response.json()["completion"]["typyComplete"])
        state = self.client.get(reverse("typy")).context["prediction_ui"]
        self.assertFalse(state["slots"][str(self.a.pk)]["predicted"])
        self.assertEqual(state["slots"][str(self.a.pk)]["goals"], "")

    def test_explicit_clear_removes_chip_that_requires_a_prediction(self):
        Prediction.objects.create(user=self.user, match=self.a, predicted_result="1")
        ChipAssignment.objects.create(user=self.user, round=self.round, match=self.a, chip="BANKER")
        response = self.save_card(result="", chip="BANKER", clear_prediction=str(self.a.pk))
        self.assertTrue(response.json()["saved"])
        self.assertFalse(Prediction.objects.filter(user=self.user, match=self.a).exists())
        self.assertFalse(ChipAssignment.objects.filter(user=self.user, match=self.a).exists())

    def test_ajax_incomplete_round_saves_without_partial_confirmation(self):
        response = self.client.post(reverse("typy"), {
            "ui_save": "1", "round_id": self.round.pk,
            f"result_{self.a.pk}": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        self.assertFalse(response.json().get("needs_confirmation", False))

    def test_goal_removal_persists_nine_and_releases_slot(self):
        for match in [self.a] + [self.match(str(i)) for i in range(9)]:
            Prediction.objects.create(user=self.user, match=match, predicted_result="1", total_goals=0)
        response = self.save_card(goals="")
        self.assertEqual(response.json()["completion"]["goalsSelected"], 9)
        self.assertIsNone(Prediction.objects.get(match=self.a).total_goals)
        self.assertTrue(self.save_card(self.b, goals="2").json()["saved"])
        self.assertEqual(self.client.get(reverse("typy")).context["prediction_ui"]["completion"]["goalsSelected"], 10)

    def test_locked_prediction_cannot_be_explicitly_cleared(self):
        prediction = Prediction.objects.create(user=self.user, match=self.a, predicted_result="1", total_goals=2)
        self.a.kickoff = timezone.now() - timedelta(minutes=5)
        self.a.save()
        self.assertEqual(self.save_card(result="", clear_prediction=str(self.a.pk)).status_code, 400)
        prediction.refresh_from_db()
        self.assertEqual((prediction.predicted_result, prediction.total_goals), ("1", 2))

    def test_future_explicit_removal_remains_empty_after_refresh(self):
        future = Round.objects.create(name="Future clear")
        Draft.objects.create(name="Future clear draft", starts_at=timezone.now(), next_round=future)
        match = self.match("Future clear match", future)
        Prediction.objects.create(user=self.user, match=match, predicted_result="2", total_goals=4)
        self.assertTrue(self.save_card(match, result="", clear_prediction=str(match.pk)).json()["saved"])
        slot = self.client.get(reverse("typy") + "?tab=future").context["prediction_ui"]["slots"][str(match.pk)]
        self.assertFalse(slot["predicted"])
        self.assertEqual(slot["goals"], "")

    def test_var_clear_does_not_delete_locked_goals(self):
        prediction = Prediction.objects.create(user=self.user, match=self.a, predicted_result="1", total_goals=2)
        ChipAssignment.objects.create(user=self.user, round=self.round, match=self.a, chip="CHANGE_MIND")
        self.a.kickoff = timezone.now() - timedelta(minutes=5)
        self.a.save()
        self.assertEqual(self.save_card(result="", chip="CHANGE_MIND", clear_prediction=str(self.a.pk)).status_code, 400)
        prediction.refresh_from_db()
        self.assertEqual(prediction.total_goals, 2)

    def test_existing_original_prediction_is_not_transferred_by_swap_action(self):
        loser = self.swap_pair()
        original = Prediction.objects.create(user=self.user, match=self.a, predicted_result="X", total_goals=3)
        response = self.save_card(chip="SWAP", result="X", goals="3", chip_action="1")
        self.assertTrue(response.json()["saved"])
        self.assertTrue(Prediction.objects.filter(pk=original.pk, match=self.a, predicted_result="X").exists())
        self.assertFalse(Prediction.objects.filter(user=self.user, match=loser).exists())
        self.assertEqual(response.json()["slots"][str(self.a.id)]["outcomes"], [])

    def test_backend_supplies_swap_eligibility(self):
        loser = self.swap_pair()
        loser.kickoff = timezone.now() - timedelta(minutes=1); loser.save()
        response = self.client.get(reverse("typy"))
        self.assertTrue(response.context["prediction_ui"]["slots"][str(self.a.id)]["chips"]["SWAP"]["reason"])
        self.assertEqual(self.save_card(chip="SWAP", result="", chip_action="1").status_code, 400)

    def test_failed_prediction_write_rolls_back_chip(self):
        with patch("matches.views.Prediction.objects.update_or_create", side_effect=ValidationError("Failed write")):
            response = self.save_card(chip="BANKER")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ChipAssignment.objects.exists())
        self.assertFalse(Prediction.objects.exists())
        self.round.refresh_from_db(); self.assertIsNone(self.round.early_bird_user_id)

    def test_change_mind_remains_editable_after_kickoff_without_changing_locked_goals(self):
        Prediction.objects.create(user=self.user, match=self.a, predicted_result="1", total_goals=3)
        ChipAssignment.objects.create(user=self.user, round=self.round, match=self.a, chip="CHANGE_MIND")
        self.a.kickoff = timezone.now() - timedelta(minutes=10); self.a.save()
        self.assertTrue(self.save_card(result="2").json()["saved"])
        self.assertEqual(Prediction.objects.get(match=self.a).predicted_result, "2")
        self.assertEqual(Prediction.objects.get(match=self.a).total_goals, 3)

    def test_ajax_whole_list_and_card_share_saved_state(self):
        response = self.client.post(reverse("typy"), {"ui_save": "1", "round_id": self.round.id,
             "confirm_less_than_ten": "1", f"result_{self.a.id}": "X", f"result_{self.b.id}": "2"})
        self.assertTrue(response.json()["saved"])
        self.assertTrue(self.save_card(result="1").json()["saved"])
        response = self.client.get(reverse("typy"))
        self.assertEqual([m.saved_prediction for m in response.context["matches"]], ["1", "2"])

    def test_card_cannot_edit_after_kickoff(self):
        self.a.kickoff = timezone.now() - timedelta(seconds=1); self.a.save()
        self.assertEqual(self.save_card().status_code, 400)
        self.assertFalse(Prediction.objects.exists())


class PredictionCompletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="completion")
        self.client.force_login(self.user)
        self.round = Round.objects.create(name="Completion", is_active=True, match_count=30)

    def match(self, name, *, editable=True):
        offset = timedelta(days=2) if editable else -timedelta(minutes=5)
        return Match.objects.create(
            round=self.round, league="League", home_team=name, away_team="Away",
            kickoff=timezone.now() + offset,
        )

    def completion(self):
        return self.client.get(reverse("typy")).context["prediction_ui"]["completion"]

    def predict(self, match, *, goals=None):
        return Prediction.objects.create(
            user=self.user, match=match, predicted_result="1", total_goals=goals,
        )

    def test_typy_completion_uses_only_actionable_matches(self):
        predicted = self.match("Predicted")
        missing = self.match("Missing")
        self.match("Locked missing", editable=False)
        self.predict(predicted)
        self.assertFalse(self.completion()["typyComplete"])
        self.predict(missing)
        self.assertTrue(self.completion()["typyComplete"])

    def test_ten_goals_complete_and_nine_incomplete_when_ten_are_achievable(self):
        matches = [self.match(f"Match {index}") for index in range(10)]
        for match in matches[:9]:
            self.predict(match, goals=2)
        state = self.completion()
        self.assertEqual((state["goalsSelected"], state["maxAchievableGole"], state["goleComplete"]), (9, 10, False))
        self.predict(matches[9], goals=2)
        state = self.completion()
        self.assertEqual((state["goalsSelected"], state["maxAchievableGole"], state["goleComplete"]), (10, 10, True))

    def test_eight_goal_ceiling_is_complete_only_after_all_eight(self):
        matches = [self.match(f"Match {index}") for index in range(8)]
        for match in matches[:7]:
            self.predict(match, goals=2)
        state = self.completion()
        self.assertEqual((state["goalsSelected"], state["maxAchievableGole"], state["goleComplete"]), (7, 8, False))
        self.predict(matches[7], goals=2)
        state = self.completion()
        self.assertEqual((state["goalsSelected"], state["maxAchievableGole"], state["goleComplete"]), (8, 8, True))

    def test_locked_saved_goals_count_and_only_remaining_legal_slots_extend_target(self):
        locked = [self.match(f"Locked {index}", editable=False) for index in range(6)]
        editable = [self.match(f"Editable {index}") for index in range(2)]
        for match in locked:
            self.predict(match, goals=3)
        self.predict(editable[0], goals=3)
        state = self.completion()
        self.assertEqual((state["goalsSelected"], state["maxAchievableGole"], state["goleComplete"]), (7, 8, False))
        self.predict(editable[1], goals=3)
        state = self.completion()
        self.assertEqual((state["goalsSelected"], state["maxAchievableGole"], state["goleComplete"]), (8, 8, True))

    def test_no_remaining_action_is_complete_despite_locked_omissions(self):
        self.match("Locked one", editable=False)
        self.match("Locked two", editable=False)
        state = self.completion()
        self.assertTrue(state["typyComplete"])
        self.assertTrue(state["goleComplete"])
        self.assertEqual(state["maxAchievableGole"], 0)

    def test_future_round_uses_the_same_completion_semantics(self):
        future = Round.objects.create(name="Future completion", match_count=30)
        Draft.objects.create(name="Future draft", starts_at=timezone.now(), next_round=future)
        match = Match.objects.create(
            round=future, league="League", home_team="Future", away_team="Away",
            kickoff=timezone.now() + timedelta(days=5),
        )
        state = self.client.get(reverse("typy") + "?tab=future").context["prediction_ui"]
        self.assertFalse(state["completion"]["typyComplete"])
        Prediction.objects.create(user=self.user, match=match, predicted_result="1", total_goals=2)
        state = self.client.get(reverse("typy") + "?tab=future").context["prediction_ui"]
        self.assertTrue(state["completion"]["typyComplete"])
        self.assertTrue(state["completion"]["goleComplete"])
        self.assertEqual(state["completion"]["maxAchievableGole"], 1)
