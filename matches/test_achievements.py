from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db import IntegrityError, transaction
from django.utils import timezone
from matches.models import (AchievementNotification, AchievementOccurrence, Match,
    MatchKickoffSnapshot, Prediction, Round, TrophyFinish, UserAchievement, UserRoundScore)
from matches.services.achievements import (evaluate_score, evaluate_trophies,
    evaluate_snapshot_achievements, _tiers, TIERS, GOAL_TIERS)
from matches.services.scoring import recalculate_round_scores
from matches.services.profile import profile_data


class AchievementEngineTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="engine")

    def finished_round(self, number, size=1):
        round_ = Round.objects.create(name=f"Round {number}", match_count=size)
        matches = [Match.objects.create(round=round_, league="L", home_team=f"A{i}",
            away_team=f"B{i}", kickoff=timezone.now()-timedelta(days=2),
            home_goals=1, away_goals=0) for i in range(size)]
        return round_, matches

    def test_repeatable_rounds_are_idempotent_and_keep_occurrences_after_five_stars(self):
        for number in range(6):
            round_, matches = self.finished_round(number, 30)
            for match in matches:
                Prediction.objects.create(user=self.user, match=match, predicted_result="2")
            recalculate_round_scores(round_)
            recalculate_round_scores(round_)
        state = UserAchievement.objects.get(user=self.user, achievement__code="TWITTER_EXPERT")
        self.assertEqual((state.progress, state.stars, state.occurrences.count()), (6,5,6))
        self.assertEqual(state.notifications.count(), 5)

    def test_incomplete_submissions_do_not_award_repeatable(self):
        round_, matches = self.finished_round(1,30)
        for match in matches[:-1]:
            Prediction.objects.create(user=self.user, match=match, predicted_result="2")
        recalculate_round_scores(round_)
        self.assertFalse(UserAchievement.objects.filter(achievement__code="TWITTER_EXPERT").exists())

    def test_tiers_persist_subthreshold_progress_without_duplicate_notifications(self):
        _tiers(self.user,"TYPY","TYPY",18,TIERS,{})
        _tiers(self.user,"GOLE","GOLE",6,GOAL_TIERS,{})
        count = AchievementNotification.objects.count()
        _tiers(self.user,"TYPY","TYPY",18,TIERS,{})
        _tiers(self.user,"GOLE","GOLE",6,GOAL_TIERS,{})
        self.assertEqual(AchievementNotification.objects.count(), count)
        state = UserAchievement.objects.get(achievement__code="TYPY",user=self.user)
        self.assertEqual((state.progress,state.current_tier,state.stars),(18,"SILVER",0))
        with patch("matches.services.achievements.evaluate_user", side_effect=AssertionError):
            self.assertEqual(profile_data(self.user)["paths"]["typy"]["progress"],18)

    def test_snapshot_uses_correct_double_pick_outcome_and_strict_population_threshold(self):
        round_, matches = self.finished_round(1)
        match = matches[0]
        score = UserRoundScore.objects.create(user=self.user,round=round_,
            breakdown=[{"effective_match":match.pk,"typy":1,"standard_prediction":["X","1"]}])
        snapshot = MatchKickoffSnapshot.objects.create(match=match,counts={"1":1,"X":99,"2":0})
        evaluate_snapshot_achievements(self.user)
        self.assertFalse(UserAchievement.objects.filter(achievement__code="LONE_WOLF").exists())
        snapshot.counts = {"1":1,"X":100,"2":0}
        snapshot.save()
        Prediction.objects.create(user=self.user,match=match,predicted_result="2")
        evaluate_snapshot_achievements(self.user)
        evaluate_snapshot_achievements(self.user)
        state = UserAchievement.objects.get(achievement__code="LONE_WOLF")
        self.assertEqual(state.context_data["submitted"],101)
        self.assertEqual(state.occurrences.count(),1)
        self.assertEqual(state.notifications.count(),1)

    def test_round_ranking_awards_only_after_complete_score_batch(self):
        round_, matches = self.finished_round(1)
        loser = get_user_model().objects.create_user(username="loser")
        Prediction.objects.create(user=loser,match=matches[0],predicted_result="2")
        Prediction.objects.create(user=self.user,match=matches[0],predicted_result="1")
        recalculate_round_scores(round_)
        recalculate_round_scores(round_)
        self.assertEqual(list(UserAchievement.objects.filter(achievement__code="ROUND_CHAMPION").values_list("user_id",flat=True)),[self.user.pk])
        self.assertEqual(TrophyFinish.objects.filter(scope="ROUND").count(),2)
        self.assertEqual(AchievementOccurrence.objects.filter(user_achievement__achievement__code="ROUND_CHAMPION").count(),1)

    def test_unfinished_round_cannot_award_champion(self):
        round_ = Round.objects.create(name="Upcoming",match_count=1)
        Match.objects.create(round=round_,league="L",home_team="A",away_team="B",kickoff=timezone.now()+timedelta(days=1))
        UserRoundScore.objects.create(user=self.user,round=round_,typy_points=10)
        evaluate_trophies(round_)
        self.assertFalse(TrophyFinish.objects.exists())
        self.assertFalse(UserAchievement.objects.filter(achievement__code="ROUND_CHAMPION").exists())

    @patch("matches.services.achievements.typy_streaks",return_value={"current_streak":10,"max_streak":10})
    def test_perfect_ten_integrates_existing_code_once(self, streaks):
        round_, _ = self.finished_round(1)
        score = UserRoundScore.objects.create(user=self.user,round=round_)
        evaluate_score(score)
        evaluate_score(score)
        state = UserAchievement.objects.get(achievement__code="PERFECT_TEN")
        self.assertEqual(state.occurrences.count(),1)
        self.assertEqual(state.notifications.count(),1)

    def test_database_rejects_duplicate_occurrence(self):
        from matches.services.achievements import _unlock
        state, _ = _unlock(self.user,"EXAMPLE","Example","ROUND",repeatable=True,event_key="round:1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AchievementOccurrence.objects.create(user_achievement=state,event_key="round:1")

    def test_migration_preserves_legacy_unlock_and_consumed_notification(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from matches.models import Achievement
        definition = Achievement.objects.create(code="TYPY_SILVER",name="TYPY",category="TYPY",tier="SILVER")
        state = UserAchievement.objects.create(user=self.user,achievement=definition,context_data={"progress":18,"round":123})
        notification = AchievementNotification.objects.create(user_achievement=state,consumed_at=timezone.now())
        migration = import_module("matches.migrations.0019_preserve_achievement_state")
        migration.preserve(apps,connection.schema_editor())
        self.assertEqual(state.occurrences.get().context,{"progress":18,"round":123})
        _tiers(self.user,"TYPY","TYPY",18,TIERS,{})
        self.assertEqual(state.notifications.count(),1)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.consumed_at)

    def test_period_awards_wait_for_period_end(self):
        from matches.services.achievements import evaluate_month_trophies
        today = timezone.localdate()
        with patch("matches.services.achievements.month_ranking") as ranking:
            evaluate_month_trophies(today.year,today.month)
            ranking.assert_not_called()
