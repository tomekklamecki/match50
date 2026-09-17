from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.core.exceptions import ValidationError

from .models import Draft, DraftPair, DraftVote, Match, Prediction, Round


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

    def test_thirty_winners_populate_next_round_and_keep_losers(self):
        self.draft.starts_at = timezone.now() - timedelta(days=7)
        self.draft.save()
        pairs = self.create_complete_draft()
        for pair in pairs:
            pair.resolve()
        next_round = self.draft.populate_next_round()
        self.assertEqual(next_round.matches.count(), 30)
        self.assertEqual(Match.objects.filter(round__isnull=True).count(), 30)
        self.draft.refresh_from_db()
        self.assertFalse(self.draft.is_active)
        self.assertTrue(all(pair.winner_id in {pair.match_a_id, pair.match_b_id} for pair in DraftPair.objects.filter(draft=self.draft)))
