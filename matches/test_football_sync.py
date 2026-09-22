import copy
import io
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, RequestFactory
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from matches.models import Competition, CompetitionSeason, Team, Match, Round, Draft, DraftPair, MatchAlternative, Prediction, ChipAssignment, UserRoundScore, UserAchievement
from matches.services.api_football import ApiFootballClient, ProviderError, normalize_fixture
from matches.services.calendar_week import CalendarWeek, current_week
from matches.services.football_sync import sync_fixtures
from matches.services.fixture_pool import in_week, draft_candidates
from matches.services.lifecycle import promote_round
from matches.services.effective_match import draft_loser_for_winner


def fixture(provider_id=9001):
    return {"fixture": {"id": provider_id, "date": "2030-09-24T18:00:00+00:00", "status": {"short": "NS"}},
        "league": {"id": 5, "season": 2030},
        "teams": {"home": {"id": 100, "name": "Poland"}, "away": {"id": 101, "name": "Germany"}},
        "goals": {"home": None, "away": None}, "score": {"fulltime": {"home": None, "away": None}}}


class FootballSyncTests(TestCase):
    def setUp(self):
        self.raw = fixture()
        self.client_api = Mock()
        self.client_api.competition.return_value = {"provider_id": 5, "name": "UEFA Nations League", "country": "World", "country_code": "", "competition_type": "Cup"}
        self.client_api.fixtures.return_value = [self.raw]

    def sync(self):
        return sync_fixtures(5, 2030, client=self.client_api)

    def test_import_reuses_canonical_entities_and_stable_fixture_id(self):
        team = Team.objects.create(name="Poland", shirt_primary="#ffffff")
        competition = Competition.objects.create(code="UNL", name="UEFA Nations League", country="World")
        first = self.sync()
        second = self.sync()
        self.assertEqual((first.fetched, first.created, second.unchanged, second.errors), (1, 1, 1, []))
        self.assertEqual((Competition.objects.count(), Team.objects.count(), Match.objects.count(), CompetitionSeason.objects.count()), (1,2,1,1))
        team.refresh_from_db()
        competition.refresh_from_db()
        self.assertEqual((team.api_football_id, team.shirt_primary, competition.api_football_id, competition.code), (100,"#ffffff",5,"UNL"))
        match = Match.objects.get()
        self.assertEqual(match.home_team_entity, team)
        self.assertIsNone(match.round_id)

    def test_league_10_friendlies_use_generic_identity_kw_and_draft_pool(self):
        existing_team = Team.objects.create(name="Poland", api_football_id=100, shirt_primary="#ffffff")
        raw = fixture(provider_id=10010)
        raw["league"] = {"id": 10, "season": 2030}
        client = Mock()
        client.competition.return_value = {
            "provider_id": 10, "name": "Friendlies", "country": "World",
            "country_code": "", "competition_type": "Cup",
        }
        client.fixtures.return_value = [raw]

        first = sync_fixtures(10, 2030, client=client)
        second = sync_fixtures(10, 2030, client=client)
        match = Match.objects.get(api_football_id=10010)
        competition = match.competition_season.competition

        self.assertEqual((first.created, second.unchanged), (1, 1))
        self.assertEqual((competition.api_football_id, competition.name), (10, "Friendlies"))
        self.assertEqual(match.home_team_entity, existing_team)
        self.assertEqual(match.competition_season.provider_season, 2030)
        self.assertEqual(match.automatic_kw, CalendarWeek.at(match.kickoff))
        self.assertEqual(match.effective_kw, match.automatic_kw)
        self.assertTrue(draft_candidates(match.effective_kw).filter(pk=match.pk).exists())

        match.kw_override_year, match.kw_override_week = 2030, 40
        match.save()
        self.assertEqual(match.effective_kw, CalendarWeek(2030, 40))

    def test_refresh_preserves_game_state_and_does_not_score(self):
        self.sync()
        match = Match.objects.get()
        round_ = Round.objects.create(name="Game", match_count=1)
        match.round = round_
        match.kw_override_year, match.kw_override_week = 2030, 40
        match.save()
        user = get_user_model().objects.create(username="player")
        prediction = Prediction.objects.create(user=user, match=match, predicted_result="1", total_goals=2)
        chip = ChipAssignment.objects.create(user=user, round=round_, match=match, chip="BANKER")
        promote_round(round_)
        frozen = round_.frozen_match_order
        self.raw["fixture"].update(date="2030-10-15T18:00:00+00:00", status={"short": "FT"})
        self.raw["goals"] = {"home":2, "away":0}
        self.raw["score"]["fulltime"] = {"home":2, "away":0}
        with self.captureOnCommitCallbacks(execute=True), patch("matches.services.scoring.recalculate_round_scores") as scorer:
            report = self.sync()
        scorer.assert_not_called()
        self.assertEqual(report.updated, 1)
        match.refresh_from_db()
        round_.refresh_from_db()
        self.assertEqual((match.round_id, round_.frozen_match_order, match.effective_kw), (round_.pk, frozen, CalendarWeek(2030,40)))
        self.assertEqual(match.provider_status, "FT")
        self.assertEqual(match.provider_score["goals"]["home"], 2)
        self.assertIsNone(match.home_goals)
        self.assertEqual(match.status, "UPCOMING")
        self.assertTrue(Prediction.objects.filter(pk=prediction.pk, total_goals=2).exists())
        self.assertTrue(ChipAssignment.objects.filter(pk=chip.pk).exists())
        self.assertFalse(UserRoundScore.objects.exists())
        self.assertFalse(UserAchievement.objects.exists())

    def test_kw_override_and_removal_follow_updated_kickoff(self):
        self.sync()
        match = Match.objects.get()
        old_week = match.automatic_kw
        match.kw_override_year, match.kw_override_week = 2030, 41
        match.save()
        self.raw["fixture"]["date"] = "2030-10-15T18:00:00Z"
        self.sync()
        match.refresh_from_db()
        self.assertNotEqual(old_week, match.automatic_kw)
        self.assertEqual(match.effective_kw, CalendarWeek(2030,41))
        self.assertEqual(in_week(Match.objects.all(), CalendarWeek(2030,41)).count(), 1)
        match.kw_override_year = match.kw_override_week = None
        match.save()
        self.assertEqual(match.effective_kw, match.automatic_kw)
        self.assertEqual(in_week(Match.objects.all(), match.automatic_kw).count(), 1)
        self.assertEqual(in_week(Match.objects.all(), CalendarWeek(2030,41)).count(), 0)

    def test_invalid_fixture_rolls_back_teams_and_reports_error(self):
        self.raw["teams"]["away"]["name"] = ""
        report = self.sync()
        self.assertEqual(len(report.errors), 1)
        self.assertFalse(Team.objects.exists())
        self.assertFalse(Match.objects.exists())

    def test_identity_conflict_does_not_reassign_game_fixture(self):
        self.sync()
        self.raw["teams"]["home"] = {"id":999, "name":"Other team"}
        self.assertEqual(len(self.sync().errors), 1)
        self.assertEqual(Match.objects.get().home_team, "Poland")
        self.assertFalse(Team.objects.filter(api_football_id=999).exists())

    def test_sync_preserves_draft_alternative_and_legacy_chip_team_label(self):
        second = fixture(9002)
        second["teams"] = {"home":{"id":102,"name":"France"},"away":{"id":103,"name":"Italy"}}
        self.client_api.fixtures.return_value = [self.raw, second]
        self.sync()
        original, alternative = list(Match.objects.order_by("api_football_id"))
        draft = Draft.objects.create(name="Origin", starts_at=timezone.now()-timedelta(days=2))
        pair = DraftPair.objects.create(draft=draft, match_a=original, match_b=alternative,
            winner=original, resolved_at=timezone.now(), resolution_method="VOTE")
        draft.resolve_closed_pairs()
        original.refresh_from_db()
        user = get_user_model().objects.create(username="goal-team")
        chip = ChipAssignment.objects.create(user=user, round=original.round, match=original, chip="GOOOOOOOOAL",goal_team="Poland")
        self.raw["teams"]["home"]["name"] = "Poland renamed"
        self.raw["fixture"]["date"] = "2030-10-15T18:00:00Z"
        self.sync()
        original.refresh_from_db()
        chip.refresh_from_db()
        pair.refresh_from_db()
        self.assertEqual(original.home_team_entity.name,"Poland renamed")
        self.assertEqual(original.home_team, chip.goal_team)
        self.assertEqual(pair.winner_id, original.pk)
        self.assertEqual(draft_loser_for_winner(original), alternative)

    def test_backfill_preserves_existing_resolved_pair_and_manual_fixture(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        original = Match.objects.create(league="L",home_team="A",away_team="B",kickoff=timezone.now()+timedelta(days=10))
        alternative = Match.objects.create(league="L",home_team="C",away_team="D",kickoff=timezone.now()+timedelta(days=10))
        draft = Draft.objects.create(name="Legacy",starts_at=timezone.now()-timedelta(days=2))
        pair = DraftPair.objects.create(draft=draft,match_a=original,match_b=alternative,
            winner=original,resolved_at=timezone.now(),resolution_method="VOTE")
        MatchAlternative.objects.all().delete()
        Match.objects.update(automatic_kw_year=None,automatic_kw_week=None)
        import_module("matches.migrations.0023_football_foundation").backfill(apps,connection.schema_editor())
        original.refresh_from_db()
        self.assertEqual(original.automatic_kw_year, original.automatic_kw.year)
        self.assertIsNone(original.api_football_id)
        self.assertEqual(MatchAlternative.objects.get(match=original).source_pair_id, pair.pk)
        self.assertEqual(draft_loser_for_winner(original), alternative)

    def test_manual_fixture_and_invalid_kw(self):
        match = Match.objects.create(league="Manual", home_team="A", away_team="B", kickoff=timezone.now()+timedelta(days=2))
        self.assertIsNone(match.api_football_id)
        match.kw_override_year, match.kw_override_week = 2021, 53
        with self.assertRaises(ValidationError):
            match.save()
        match.kw_override_week = None
        with self.assertRaises(ValidationError):
            match.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Match.objects.filter(pk=match.pk).update(kw_override_year=2021)

    def test_admin_filter_and_calendar_context(self):
        self.sync()
        user = get_user_model().objects.create_superuser(username="admin", password="test", email="admin@example.com")
        self.client.force_login(user)
        week = Match.objects.get().effective_kw
        response = self.client.get(reverse("admin:matches_match_changelist"), {
            "effective_kw":f"{week.year}-{week.week}",
            "competition_season__competition__id__exact": Competition.objects.get().pk,
            "competition_season__provider_season": 2030,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Poland")
        self.assertEqual(response.context["current_kw"], current_week())
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_command_reports_counts_and_second_run_is_unchanged(self):
        with patch("matches.services.football_sync.ApiFootballClient", return_value=self.client_api):
            output = io.StringIO()
            call_command("sync_football_fixtures", competition=5, season=2030, stdout=output)
            self.assertIn("created: 1", output.getvalue())
            output = io.StringIO()
            call_command("sync_football_fixtures", competition=5, season=2030, stdout=output)
            self.assertIn("unchanged: 1", output.getvalue())
            self.raw["fixture"]["date"] = "not-a-date"
            with self.assertRaises(CommandError):
                call_command("sync_football_fixtures", competition=5, season=2030, stdout=io.StringIO(), stderr=io.StringIO())


class CalendarWeekTests(TestCase):
    def test_warsaw_iso_boundary_and_shift(self):
        self.assertEqual(CalendarWeek.at(datetime(2021,1,1,tzinfo=dt_timezone.utc)), CalendarWeek(2020,53))
        self.assertEqual(CalendarWeek.at(datetime(2021,1,3,23,30,tzinfo=dt_timezone.utc)), CalendarWeek(2021,1))
        self.assertEqual(CalendarWeek(2020,53).shift(), CalendarWeek(2021,1))
        self.assertEqual(current_week(datetime(2021,1,1,tzinfo=dt_timezone.utc)), CalendarWeek(2020,53))
        with self.assertRaises(ValueError):
            CalendarWeek(2021,53)


class ProviderClientTests(TestCase):
    def client_with(self, payload):
        opener = Mock(return_value=io.StringIO(json.dumps(payload)))
        return ApiFootballClient(key="test-key", opener=opener), opener

    def test_request_filters_and_header(self):
        client, opener = self.client_with({"errors":[], "response":[fixture()], "paging":{"total":1}})
        rows = client.fixtures(5,2030,date_from="2030-09-24",date_to="2030-09-27")
        request = opener.call_args.args[0]
        self.assertIn("league=5&season=2030", request.full_url)
        self.assertIn("from=2030-09-24", request.full_url)
        self.assertEqual(request.headers["X-apisports-key"], "test-key")
        self.assertEqual(normalize_fixture(rows[0]).provider_id, 9001)

    def test_provider_errors_pagination_and_network_are_safe(self):
        for payload in [{"errors":{"token":"secret"}, "response":[]}, {"response":[], "paging":{"total":2}}, {"response":{}}]:
            client, _ = self.client_with(payload)
            with self.assertRaises(ProviderError) as error:
                client.fixtures(5,2030)
            self.assertNotIn("secret", str(error.exception))
        for error in [URLError("secret"), HTTPError("url",429,"secret",{},None), TimeoutError()]:
            client = ApiFootballClient(key="test-key", opener=Mock(side_effect=error))
            with self.assertRaises(ProviderError):
                client.fixtures(5,2030)


class AlternativeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="swap")
        self.round = Round.objects.create(name="Bootstrap",match_count=30)
        self.matches = [Match.objects.create(league="L",home_team=f"A{i}",away_team="B",kickoff=timezone.now()+timedelta(days=10)) for i in range(34)]
        Match.objects.filter(pk__in=[m.pk for m in self.matches[:30]]).update(round=self.round)
        for match in self.matches[:30]:
            match.refresh_from_db()

    def test_bootstrap_exactly_four_swap_slots_and_backend_rejects_rest(self):
        promote_round(self.round)
        for index in range(4):
            MatchAlternative.objects.create(match=self.matches[index],alternative=self.matches[30+index])
        for index, match in enumerate(self.matches[:30]):
            alternative = draft_loser_for_winner(match)
            chip = ChipAssignment(user=self.user, round=self.round, match=match,chip="SWAP",replacement_match=alternative)
            if index < 4:
                chip.full_clean()
                self.assertEqual(alternative, self.matches[30+index])
            else:
                with self.assertRaises(ValidationError):
                    chip.full_clean()

    def test_draft_resolution_creates_same_relationship(self):
        draft = Draft.objects.create(name="Draft",starts_at=timezone.now()-timedelta(days=2))
        pair = DraftPair.objects.create(draft=draft,match_a=self.matches[30],match_b=self.matches[31])
        pair.resolve()
        relation = MatchAlternative.objects.get(match_id=pair.winner_id)
        self.assertEqual(relation.source_pair_id, pair.pk)
        self.assertEqual(draft_loser_for_winner(pair.winner), relation.alternative)
        draft.resolve_closed_pairs()
        pair.winner.refresh_from_db()
        self.assertEqual(pair.winner.round_id, draft.next_round_id)

    def test_cancelled_alternative_and_wrong_target_rejected(self):
        promote_round(self.round)
        MatchAlternative.objects.create(match=self.matches[0],alternative=self.matches[30])
        chip = ChipAssignment(user=self.user,round=self.round,match=self.matches[0],chip="SWAP",replacement_match=self.matches[31])
        with self.assertRaises(ValidationError):
            chip.full_clean()
        Match.objects.filter(pk=self.matches[30].pk).update(provider_status="CANC")
        chip.replacement_match = self.matches[30]
        with self.assertRaises(ValidationError):
            chip.full_clean()

    def test_admin_bootstrap_selects_thirty_and_preserves_four_candidates(self):
        from matches.admin import MatchAdmin
        Match.objects.update(round=None)
        model_admin = MatchAdmin(Match, admin.site)
        request = RequestFactory().post("/admin/matches/match/")
        selected = Match.objects.filter(pk__in=[m.pk for m in self.matches[:30]])
        with patch.object(model_admin, "message_user"):
            model_admin.bootstrap_round(request, selected)
            created = Round.objects.get(name="Initial football Round")
            self.assertFalse(created.is_active)
            self.assertEqual(created.matches.count(), 30)
            self.assertEqual(draft_candidates().count(), 4)
            model_admin.bootstrap_round(request, selected)
            self.assertEqual(Round.objects.filter(name="Initial football Round").count(), 1)


class MatchAlternativeAdminTests(TestCase):
    def setUp(self):
        from matches.admin import MatchAlternativeForm

        self.form_class = MatchAlternativeForm
        self.round = Round.objects.create(name="Active alternatives", match_count=30)
        kickoff = timezone.now() + timedelta(days=10)
        self.round_matches = [
            Match.objects.create(league="Round", home_team=f"Home {index}", away_team="Away", kickoff=kickoff)
            for index in range(30)
        ]
        Match.objects.filter(pk__in=[match.pk for match in self.round_matches]).update(round=self.round)
        self.candidates = [
            Match.objects.create(league=f"Other {index}", home_team=f"Candidate {index}", away_team="Away", kickoff=kickoff)
            for index in range(4)
        ]
        promote_round(self.round)
        for match in self.round_matches:
            match.refresh_from_db()

    def test_add_form_scopes_matches_and_candidates_to_active_round_week(self):
        form = self.form_class()
        self.assertQuerySetEqual(form.fields["match"].queryset, self.round_matches, ordered=False)
        self.assertQuerySetEqual(form.fields["alternative"].queryset, self.candidates, ordered=False)

    def test_active_round_and_different_week_matches_are_not_alternatives(self):
        different_week = Match.objects.create(
            league="Other", home_team="Different KW", away_team="Away",
            kickoff=self.candidates[0].kickoff + timedelta(days=14),
        )
        alternative_ids = set(self.form_class().fields["alternative"].queryset.values_list("pk", flat=True))
        self.assertFalse(alternative_ids.intersection(match.pk for match in self.round_matches))
        self.assertNotIn(different_week.pk, alternative_ids)

    def test_consumed_alternative_cannot_be_reused_in_active_round(self):
        MatchAlternative.objects.create(match=self.round_matches[0], alternative=self.candidates[0])
        form = self.form_class()
        self.assertNotIn(self.round_matches[0], form.fields["match"].queryset)
        self.assertNotIn(self.candidates[0], form.fields["alternative"].queryset)
        with self.assertRaises(ValidationError):
            MatchAlternative(match=self.round_matches[1], alternative=self.candidates[0]).full_clean()

    def test_backend_rejects_invalid_round_and_effective_week(self):
        different_week = Match.objects.create(
            league="Other", home_team="Wrong KW", away_team="Away",
            kickoff=self.candidates[0].kickoff + timedelta(days=14),
        )
        outside_match = Match.objects.create(
            league="Other", home_team="Outside", away_team="Away", kickoff=self.candidates[0].kickoff,
        )
        for relation in (
            MatchAlternative(match=self.round_matches[0], alternative=self.round_matches[1]),
            MatchAlternative(match=self.round_matches[0], alternative=different_week),
            MatchAlternative(match=outside_match, alternative=self.candidates[0]),
        ):
            with self.assertRaises(ValidationError):
                relation.full_clean()

    def test_historical_relationship_remains_visible_and_cannot_be_mutated(self):
        from matches.admin import MatchAlternativeAdmin

        relation = MatchAlternative.objects.create(match=self.round_matches[0], alternative=self.candidates[0])
        Round.objects.filter(pk=self.round.pk).update(is_active=False)
        relation.refresh_from_db()
        relation.full_clean()
        model_admin = MatchAlternativeAdmin(MatchAlternative, admin.site)
        self.assertEqual(
            model_admin.get_readonly_fields(RequestFactory().get("/admin/"), relation),
            ("match", "alternative", "source_pair"),
        )
        relation.alternative = self.candidates[1]
        with self.assertRaises(ValidationError):
            relation.full_clean()

    def test_no_active_or_multi_week_round_exposes_no_global_fallback(self):
        Round.objects.filter(pk=self.round.pk).update(is_active=False)
        no_active = self.form_class()
        self.assertFalse(no_active.fields["match"].queryset.exists())
        self.assertFalse(no_active.fields["alternative"].queryset.exists())
        Round.objects.filter(pk=self.round.pk).update(is_active=True)
        Match.objects.filter(pk=self.round_matches[0].pk).update(
            kw_override_year=self.round_matches[0].effective_kw.shift().year,
            kw_override_week=self.round_matches[0].effective_kw.shift().week,
        )
        mixed_week = self.form_class()
        self.assertFalse(mixed_week.fields["match"].queryset.exists())
        self.assertFalse(mixed_week.fields["alternative"].queryset.exists())
