from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from matches.models import AchievementOccurrence, AchievementNotification, Match, Prediction, Round, UserAchievement, UserRoundScore
from matches.services.achievements import _tiers, _unlock, TIERS, GOAL_TIERS, STREAK_TIERS, REPEAT_TIERS, MASTERY_TIERS
from matches.services.core_progression import rebuild_core, rebuild_round_core, rebuild_streak_repeat, repeat_progress, core_achievement_points
from matches.services.match_order import freeze_match_order
from matches.services.profile import profile_data


class CoreProgressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")

    def score(self, number, typy=18, gole=5):
        round_ = Round.objects.create(name=f"Core {number}", match_count=1,
            ranking_date=timezone.localdate()+timedelta(days=number))
        match = Match.objects.create(round=round_, league="L", home_team="A", away_team="B",
            kickoff=timezone.now()-timedelta(days=1), home_goals=1, away_goals=0)
        return UserRoundScore.objects.create(user=self.user, round=round_, typy_points=typy,
            gole_points=gole, breakdown=[{"match": match.pk, "typy": 1}])

    def state(self, code):
        return UserAchievement.objects.get(user=self.user, achievement__code=code)

    def test_all_core_thresholds_and_two_points_per_tier(self):
        for code, tiers, expected in [("TYPY", TIERS, [10,14,18,22,26,30]),
            ("GOLE", GOAL_TIERS, [1,3,5,7,9,10]), ("STREAK", STREAK_TIERS, [4,8,12,16,20,25])]:
            self.assertEqual([threshold for _, threshold in tiers], expected)
            for index, (tier, threshold) in enumerate(tiers):
                _tiers(self.user, code, code, threshold-1, tiers, {})
                self.assertNotEqual(self.state(code).current_tier, tier)
                _tiers(self.user, code, code, threshold, tiers, {})
                self.assertEqual(self.state(code).current_tier, tier)
                self.assertEqual(UserAchievement.objects.filter(user=self.user, achievement__category=code, unlocked=True).count(), index+1)
        self.assertEqual(core_achievement_points(self.user), 36)

    def test_discovery_is_not_first_completion_and_repeats_are_idempotent(self):
        self.score(1)
        rebuild_round_core(self.user)
        for code in ["TYPY_AGAIN", "GOLE_AGAIN"]:
            self.assertTrue(self.state(code).context_data["discovered"])
            self.assertEqual(self.state(code).progress, 0)
            self.assertFalse(self.state(code).unlocked)
        self.score(2, typy=19, gole=6)
        rebuild_round_core(self.user)
        counts = (AchievementOccurrence.objects.count(), AchievementNotification.objects.count())
        rebuild_round_core(self.user)
        self.assertEqual(counts, (AchievementOccurrence.objects.count(), AchievementNotification.objects.count()))
        for code in ["TYPY_AGAIN", "GOLE_AGAIN"]:
            self.assertEqual((self.state(code).progress, self.state(code).current_tier), (1, "BRONZE"))

    def test_repeat_thresholds_through_godlike(self):
        self.assertEqual([n for _, n in REPEAT_TIERS], [1,3,8,15,25,50])
        for code in ["TYPY_AGAIN", "GOLE_AGAIN", "STREAK_AGAIN"]:
            for tier, count in REPEAT_TIERS:
                repeat_progress(self.user, code, code, [f"event:{n}" for n in range(count+1)])
                self.assertEqual((self.state(code).progress, self.state(code).current_tier), (count, tier))
        self.assertEqual(core_achievement_points(self.user), 36)

    def test_independent_streaks_not_each_match_count_as_completions(self):
        round_ = Round.objects.create(name="Runs", match_count=30)
        matches = []
        for number in range(30):
            match = Match.objects.create(round=round_, league="L", home_team=f"{number:02}", away_team="B",
                kickoff=timezone.now().replace(microsecond=0)+timedelta(minutes=number), home_goals=1, away_goals=0)
            Prediction.objects.create(user=self.user, match=match, predicted_result="2" if number == 12 else "1")
            matches.append(match)
        freeze_match_order(round_)
        Match.objects.filter(pk__in=[m.pk for m in matches[12:]]).update(status="UPCOMING")
        rebuild_streak_repeat(self.user)
        self.assertEqual(self.state("STREAK_AGAIN").progress, 0)
        self.assertTrue(self.state("STREAK_AGAIN").context_data["discovered"])
        Match.objects.filter(pk__in=[m.pk for m in matches[12:]]).update(status="FINISHED")
        rebuild_streak_repeat(self.user)
        rebuild_streak_repeat(self.user)
        state = self.state("STREAK_AGAIN")
        self.assertEqual((state.progress, state.current_tier, state.occurrences.count()), (1, "BRONZE", 2))

    def test_public_profile_uses_owner_discovery_and_never_leaks_mystery(self):
        viewer = get_user_model().objects.create_user(username="viewer")
        repeat_progress(viewer, "TYPY_AGAIN", "TYPY — DO IT AGAIN", ["round:999"])
        self.client.force_login(viewer)
        response = self.client.get(reverse("player_profile", args=[self.user.username]))
        self.assertContains(response, "???????", count=3)
        self.assertNotContains(response, "DO IT AGAIN")
        paths = profile_data(self.user)["paths"]
        self.assertEqual(list(paths), ["typy", "typy_again", "gole", "gole_again", "streak", "streak_again"])
        self.assertEqual([paths[key]["next"][1] for key in ["typy", "gole", "streak"]], [10,1,4])
        self.score(1)
        rebuild_core(self.user)
        self.client.logout()
        response = self.client.get(reverse("player_profile", args=[self.user.username]))
        self.assertContains(response, "TYPY — DO IT AGAIN")
        self.assertContains(response, "GOLE — DO IT AGAIN")
        self.assertContains(response, "???????", count=1)

    def test_profile_zero_and_core_progress_presentation(self):
        response = self.client.get(reverse("player_profile", args=[self.user.username]))

        self.assertContains(response, "0 / 0", count=2)
        self.assertContains(response, "<b>0%</b>", count=2)
        self.assertContains(response, "BRAK RANGI", count=12)
        self.assertContains(response, '<div class="progress-meta progress-values"><span>0</span><span>10 do BRONZE</span></div>')
        self.assertContains(response, '<div class="progress-meta progress-values"><span>0</span><span>1 do BRONZE</span></div>')
        self.assertContains(response, '<div class="progress-meta progress-values"><span>0</span><span>4 do BRONZE</span></div>')
        self.assertContains(response, '<div class="progress-meta"><span>0</span><span>10 do BRONZE</span></div>', count=9)
        self.assertContains(response, 'class="profile-info-icon" tabindex="0"')
        self.assertContains(response, 'class="profile-info-tooltip" role="tooltip" hidden')
        self.assertContains(response, 'Suma poprawnych typów w danych rozgrywkach')
        self.assertContains(response, 'class="mastery-more"')
        self.assertContains(response, 'POKAŻ POZOSTAŁE 4')
        self.assertNotContains(response, "0 / 10")
        self.assertContains(response, "???????", count=3)

        _tiers(self.user, "TYPY", "TYPY", 10, TIERS, {})
        response = self.client.get(reverse("player_profile", args=[self.user.username]))
        self.assertContains(response, "BRONZE")
        self.assertContains(response, '<div class="progress-meta progress-values"><span>10</span><span>14 do SILVER</span></div>')

    def test_backfill_preserves_ledger_and_non_core_and_corrects_old_tiers(self):
        _tiers(self.user, "TYPY", "TYPY", 30, TIERS, {})
        secret, _ = _unlock(self.user, "TEST_SECRET", "Secret", "HIDDEN", hidden=True)
        _tiers(self.user, "MASTERY_EPL", "EPL", 30, MASTERY_TIERS, {})
        self.score(1, typy=18, gole=5)
        old_occurrences = set(AchievementOccurrence.objects.values_list("pk", flat=True))
        AchievementNotification.objects.update(consumed_at=timezone.now())
        notifications = dict(AchievementNotification.objects.values_list("pk", "consumed_at"))
        mastery_before = self.state("MASTERY_EPL").context_data
        call_command("rebuild_core", stdout=StringIO())
        self.assertEqual(self.state("TYPY").current_tier, "GOLD")
        self.assertEqual(core_achievement_points(self.user), 12)
        self.assertTrue(old_occurrences.issubset(set(AchievementOccurrence.objects.values_list("pk", flat=True))))
        for pk, consumed in notifications.items():
            self.assertEqual(AchievementNotification.objects.get(pk=pk).consumed_at, consumed)
        secret.refresh_from_db()
        self.assertTrue(secret.unlocked)
        self.assertEqual(self.state("MASTERY_EPL").context_data, mastery_before)
        counts = (AchievementOccurrence.objects.count(), AchievementNotification.objects.count())
        call_command("rebuild_core", stdout=StringIO())
        self.assertEqual(counts, (AchievementOccurrence.objects.count(), AchievementNotification.objects.count()))
