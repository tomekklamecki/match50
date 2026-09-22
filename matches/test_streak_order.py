from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from matches.models import Round, Match, Prediction, ChipAssignment, UserAchievement
from matches.services.lifecycle import promote_round
from matches.services.match_order import ordered_matches, freeze_match_order
from matches.services.streaks import typy_streaks
from matches.services.scoring import recalculate_user_round_score


class FrozenStreakTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="sequence")

    def fixture(self, round_, name, hours=0, result=True):
        match = Match.objects.create(round=round_, league="L", home_team=name, away_team="Visitors",
            kickoff=timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)+timedelta(days=1, hours=hours),
            home_goals=1 if result else None, away_goals=0 if result else None)
        Prediction.objects.create(user=self.user, match=match, predicted_result="1", total_goals=9)
        return match

    def test_future_order_and_promotion_freeze_survive_kickoff_edits(self):
        round_ = Round.objects.create(name="Future", match_count=3)
        z = self.fixture(round_, "Zulu", result=False)
        a = self.fixture(round_, " alpha ", result=False)
        first = self.fixture(round_, "Last alphabetically", hours=-1, result=False)
        self.assertEqual([m.pk for m in ordered_matches(round_)], [first.pk, a.pk, z.pk])
        before = list(Prediction.objects.values_list("pk", "match_id", "predicted_result", "total_goals"))
        promote_round(round_)
        z.kickoff -= timedelta(days=2)
        z.save()
        round_.refresh_from_db()
        self.assertEqual([m.pk for m in ordered_matches(round_)], [first.pk, a.pk, z.pk])
        self.assertEqual(before, list(Prediction.objects.values_list("pk", "match_id", "predicted_result", "total_goals")))

    def test_cross_round_streak_and_achievement_use_ranking_date_not_id(self):
        newer = Round.objects.create(name="New", match_count=4)
        older = Round.objects.create(name="Old", match_count=4, ranking_date=timezone.localdate()-timedelta(days=7))
        old_matches = [self.fixture(older, str(i)) for i in range(4)]
        Prediction.objects.filter(match=old_matches[0]).update(predicted_result="2")
        new_matches = [self.fixture(newer, str(i), result=i < 3) for i in range(4)]
        freeze_match_order(older)
        promote_round(newer)
        self.assertEqual(typy_streaks(self.user), {"current_streak":6,"max_streak":6})
        recalculate_user_round_score(self.user, newer)
        state = UserAchievement.objects.get(user=self.user, achievement__code="STREAK")
        self.assertEqual(state.context_data["current_streak"], 6)
        self.assertEqual(state.current_tier, "BRONZE")
        last = new_matches[-1]
        last.home_goals, last.away_goals = 0, 1
        last.save()
        self.assertEqual(typy_streaks(self.user), {"current_streak":0,"max_streak":6})

    def test_double_pick_success_counts_even_when_base_pick_is_wrong(self):
        round_ = Round.objects.create(name="Chips", match_count=2)
        a = self.fixture(round_, "A", result=False)
        b = self.fixture(round_, "B", result=False)
        ChipAssignment.objects.create(user=self.user, round=round_, match=b, chip="DOUBLE_PICK", outcomes=["1","2"])
        Prediction.objects.filter(match=b).update(predicted_result="2", total_goals=1)
        promote_round(round_)
        for match in [b, a]:
            match.home_goals, match.away_goals = 1, 0
            match.save()
        score = recalculate_user_round_score(self.user, round_)
        self.assertEqual(score.typy_points, 2)
        self.assertEqual(typy_streaks(self.user), {"current_streak":2,"max_streak":2})

    def test_maximum_can_exceed_thirty(self):
        for number in range(2):
            round_ = Round.objects.create(name=str(number), match_count=16,
                ranking_date=timezone.localdate()+timedelta(days=number))
            for i in range(16):
                self.fixture(round_, str(i))
            freeze_match_order(round_)
        self.assertEqual(typy_streaks(self.user), {"current_streak":32,"max_streak":32})

    def test_var_uses_final_saved_prediction_in_both_directions(self):
        for initial, final, expected in [("2", "1", 2), ("1", "2", 0)]:
            with self.subTest(initial=initial):
                round_ = Round.objects.create(name="VAR", match_count=2)
                a = self.fixture(round_, "A", hours=24, result=False)
                b = self.fixture(round_, "B", hours=24, result=False)
                ChipAssignment.objects.create(user=self.user, round=round_, match=b, chip="CHANGE_MIND")
                prediction = Prediction.objects.get(user=self.user, match=b)
                prediction.predicted_result = initial
                prediction.save()
                prediction.predicted_result = final
                prediction.save()
                promote_round(round_)
                for match in [a, b]:
                    match.home_goals, match.away_goals = 1, 0
                    match.save()
                score = recalculate_user_round_score(self.user, round_)
                self.assertEqual(score.breakdown[-1]["typy"], int(final == "1"))
                self.assertEqual(typy_streaks(self.user)["current_streak"], expected)

    def test_double_pick_across_round_boundary_and_miss_despite_goals_bonus(self):
        older = Round.objects.create(name="Previous", match_count=1, ranking_date=timezone.localdate()-timedelta(days=7))
        self.fixture(older, "Previous")
        freeze_match_order(older)
        newer = Round.objects.create(name="Next", match_count=2)
        a = self.fixture(newer, "A", hours=24, result=False)
        b = self.fixture(newer, "B", hours=24, result=False)
        chip = ChipAssignment.objects.create(user=self.user, round=newer, match=a, chip="DOUBLE_PICK", outcomes=["1", "X"])
        ChipAssignment.objects.create(user=self.user, round=newer, match=b, chip="GOOOOOOOOAL", goal_team=b.home_team)
        Prediction.objects.filter(match=b).update(predicted_result="2", total_goals=2)
        promote_round(newer)
        a.home_goals, a.away_goals = 0, 0
        a.save()
        score = recalculate_user_round_score(self.user, newer)
        self.assertEqual(score.typy_points, 1)
        self.assertEqual(typy_streaks(self.user)["current_streak"], 2)
        b.home_goals, b.away_goals = 2, 0
        b.save()
        score = recalculate_user_round_score(self.user, newer)
        self.assertEqual((score.typy_points, score.gole_points, score.bonus_points), (1, 1, 1))
        self.assertEqual(typy_streaks(self.user), {"current_streak":0, "max_streak":2})
        ChipAssignment.objects.filter(pk=chip.pk).update(outcomes=["1", "2"])
        score = recalculate_user_round_score(self.user, newer)
        self.assertEqual(score.typy_points, 0)
        self.assertEqual(typy_streaks(self.user), {"current_streak":0, "max_streak":1})

    def test_model_activation_freezes_order(self):
        round_ = Round.objects.create(name="Admin activation", match_count=1)
        match = self.fixture(round_, "A", result=False)
        round_.is_active = True
        round_.save()
        round_.refresh_from_db()
        self.assertEqual(round_.frozen_match_order, [match.pk])

    def test_backfill_freezes_history_but_leaves_future_dynamic(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        past = Round.objects.create(name="Past", match_count=2)
        b = self.fixture(past, "B")
        a = self.fixture(past, "a")
        future = Round.objects.create(name="Future", match_count=1)
        self.fixture(future, "Future", result=False)
        import_module("matches.migrations.0022_round_frozen_match_order").backfill(apps, connection.schema_editor())
        past.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(past.frozen_match_order, [a.pk, b.pk])
        self.assertIsNone(future.frozen_match_order)

    def test_rebuild_is_idempotent_and_skips_cancelled_unpredicted_slots(self):
        from matches.services.achievements import refresh_streak_progress
        from matches.models import AchievementNotification
        round_ = Round.objects.create(name="Skipped slots", match_count=5)
        matches = [self.fixture(round_, str(i)) for i in range(5)]
        Prediction.objects.filter(match=matches[1]).delete()
        Match.objects.filter(pk=matches[3].pk).update(status="CANCELLED")
        freeze_match_order(round_)
        self.assertEqual(refresh_streak_progress(self.user), {"current_streak":3,"max_streak":3})
        count = AchievementNotification.objects.count()
        refresh_streak_progress(self.user)
        self.assertEqual(AchievementNotification.objects.count(), count)
