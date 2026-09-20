from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ChipAssignment, Draft, DraftPair, Match, Prediction, Round


class PredictionCardTests(TestCase):
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

    def test_goals_need_prediction_and_partial_confirmation(self):
        self.assertEqual(self.save_card(result="", goals="3").status_code, 400)
        response = self.save_card(goals="3", confirm_less_than_ten="")
        self.assertTrue(response.json()["needs_confirmation"])
        self.assertFalse(Prediction.objects.exists())

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

    def test_backend_supplies_swap_eligibility(self):
        loser = self.swap_pair()
        loser.kickoff = timezone.now() - timedelta(minutes=1); loser.save()
        response = self.client.get(reverse("typy"))
        self.assertTrue(response.context["prediction_ui"]["slots"][str(self.a.id)]["chips"]["SWAP"]["reason"])
        self.assertEqual(self.save_card(chip="SWAP").status_code, 400)

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
