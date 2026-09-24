from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Achievement, Round, UserAchievement, UserRoundScore
from .services.profile import highest_core_ranks, profile_data


class RankingFrameTests(TestCase):
    def setUp(self):
        self.player = get_user_model().objects.create_user(username="ranked")

    def state(self, code, progress, discovered=False):
        achievement, _ = Achievement.objects.get_or_create(code=code, defaults={"name": code})
        return UserAchievement.objects.create(
            user=self.player, achievement=achievement, progress=progress,
            context_data={"discovered": discovered},
        )

    def test_all_materials_match_profile_thresholds(self):
        state = self.state("TYPY", 0)
        for value, tier in ((0, "NONE"), (10, "BRONZE"), (14, "SILVER"),
                            (18, "GOLD"), (22, "PLATINUM"), (26, "DIAMOND"), (30, "GODLIKE")):
            with self.subTest(tier=tier):
                state.progress = value
                state.save(update_fields=["progress"])
                self.assertEqual(highest_core_ranks([self.player.pk])[self.player.pk], tier)
                profile_tier = profile_data(self.player)["paths"]["typy"]["tier"]
                self.assertEqual(profile_tier, "—" if tier == "NONE" else tier)

    def test_highest_visible_core_only_and_batched_lookup(self):
        empty = get_user_model().objects.create_user(username="empty")
        self.state("TYPY", 14)
        self.state("GOLE", 7)
        hidden = self.state("TYPY_AGAIN", 50)
        self.state("MASTERY_EPL", 999)
        with self.assertNumQueries(1):
            self.assertEqual(highest_core_ranks([self.player.pk, empty.pk]),
                             {self.player.pk: "PLATINUM", empty.pk: "NONE"})
        hidden.context_data = {"discovered": True}
        hidden.save(update_fields=["context_data"])
        self.assertEqual(highest_core_ranks([self.player.pk])[self.player.pk], "GODLIKE")

    def test_ranking_renders_material_without_changing_score(self):
        round_ = Round.objects.create(name="Frame test")
        UserRoundScore.objects.create(user=self.player, round=round_, typy_points=4, gole_points=2)
        self.state("STREAK", 12)
        self.client.force_login(self.player)
        response = self.client.get(reverse("rankings"), {"round": round_.pk})
        self.assertContains(response, 'data-rank="GOLD"')
        self.assertContains(response, 'ranking-you')
        entry = response.context["entries"][0]
        self.assertEqual((entry["rank"], entry["total"], entry["tier"]), (1, 6, "GOLD"))
