from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from matches.models import Team, Match, Round, MatchAlternative, ChipAssignment
from matches.services.api_football import ProviderError
from matches.services.team_form import forms_for_matches, football_score
from matches.services.team_history import teams_for_round_history, sync_team_history, sync_round_team_history
from matches.services.lifecycle import promote_round


class TeamFormTests(TestCase):
    def setUp(self):
        self.home = Team.objects.create(name='Netherlands', api_football_id=1118)
        self.away = Team.objects.create(name='Poland', api_football_id=24)
        self.now = timezone.now()
        self.target = self.match(self.now + timedelta(days=2))

    def match(self, kickoff, **kwargs):
        return Match.objects.create(home_team='Netherlands', away_team='Poland',
            home_team_entity=self.home, away_team_entity=self.away,
            kickoff=kickoff, league='Test', **kwargs)

    def history(self, days, home, away, status='FT', **kwargs):
        return self.match(self.now - timedelta(days=days), api_football_id=1000 + days,
            provider_status=status, provider_score={'goals': {'home': home, 'away': away}, **kwargs})

    def test_last_five_order_perspective_and_strict_cutoff(self):
        for days, home, away in [(7, 5, 0), (6, 1, 0), (5, 0, 1), (4, 1, 1), (3, 2, 0), (2, 0, 3)]:
            self.history(days, home, away)
        self.history(1, 9, 0, 'PST')
        self.match(self.target.kickoff, api_football_id=999, provider_status='FT', provider_score={'goals': {'home': 9, 'away': 0}})
        with self.assertNumQueries(1):
            forms = forms_for_matches([self.target])[self.target.pk]
        self.assertEqual([x['result'] for x in forms['homeForm']], ['W', 'L', 'D', 'W', 'L'])
        self.assertEqual([x['result'] for x in forms['awayForm']], ['L', 'W', 'D', 'L', 'W'])
        self.assertEqual(forms['homeForm'][-1]['tooltip'], 'Netherlands – Poland 0:3')
        earlier = self.match(self.now - timedelta(days=4))
        self.assertEqual(len(forms_for_matches([earlier])[earlier.pk]['homeForm']), 3)

    def test_penalties_use_football_score_and_fewer_than_five(self):
        self.history(2, 1, 1, 'PEN', periods={'penalty': {'home': 5, 'away': 4}, 'extratime': {'home': 1, 'away': 1}})
        extra = self.history(1, 3, 2, 'AET', periods={'extratime': {'home': 3, 'away': 2}})
        forms = forms_for_matches([self.target])[self.target.pk]
        self.assertEqual([x['result'] for x in forms['homeForm']], ['D', 'W'])
        self.assertEqual(football_score(extra), (3, 2))
        for status in ['NS', 'LIVE', 'PST', 'CANC', 'ABD', 'AWD', 'WO']:
            extra.provider_status = status
            self.assertIsNone(football_score(extra))
        extra.provider_status, extra.provider_score = 'FT', {'goals': {'home': None, 'away': 2}}
        self.assertIsNone(football_score(extra))

    def test_round_and_alternative_teams_deduplicate_and_activation_is_local(self):
        round_ = Round.objects.create(name='Round', match_count=1)
        self.target.round = round_
        self.target.save()
        other = Team.objects.create(name='France', api_football_id=27)
        alternative = self.match(self.target.kickoff)
        alternative.away_team_entity = other
        alternative.save()
        # Relationship setup bypasses manual bootstrap restrictions; collection
        # is also used for resolved-Draft relationships.
        MatchAlternative.objects.bulk_create([MatchAlternative(match=self.target, alternative=alternative)])
        self.assertEqual(set(teams_for_round_history(round_)), {self.home, self.away, other})
        with patch('matches.services.api_football.ApiFootballClient.get', side_effect=AssertionError('Network in activation')):
            promote_round(round_)
        round_.refresh_from_db()
        self.assertIsNotNone(round_.team_history_requested_at)
        self.assertIsNone(round_.team_history_synced_at)

    def test_karta_payload_follows_swap_with_no_provider_calls(self):
        round_ = Round.objects.create(name='Round', is_active=True)
        self.target.round = round_
        self.target.save()
        self.history(1, 2, 0)
        other = Team.objects.create(name='France', api_football_id=27)
        alternative = self.match(self.target.kickoff)
        alternative.home_team_entity, alternative.home_team = other, 'France'
        alternative.save()
        MatchAlternative.objects.bulk_create([MatchAlternative(match=self.target, alternative=alternative)])
        user = get_user_model().objects.create_user('form-player')
        self.client.force_login(user)
        with patch('matches.services.api_football.ApiFootballClient.get', side_effect=AssertionError('Network in UI')):
            response = self.client.get(reverse('typy'))
            slot = response.context['prediction_ui']['slots'][str(self.target.pk)]
            self.assertEqual(slot['original']['homeForm'][0]['result'], 'W')
            self.assertEqual(slot['replacement']['homeForm'], [])
            self.assertEqual(slot['replacement']['awayForm'][0]['result'], 'L')
            ChipAssignment.objects.create(user=user, round=round_, match=self.target, chip='SWAP', replacement_match=alternative)
            response = self.client.get(reverse('typy'))
            self.assertEqual(response.context['prediction_ui']['slots'][str(self.target.pk)]['chip'], 'SWAP')


class TeamHistorySyncTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name='Netherlands', api_football_id=1118)
        self.client = Mock()
        self.row = {'fixture': {'id': 900, 'date': '2026-06-01T18:00:00Z', 'status': {'short': 'FT'}},
            'league': {'id': 10, 'season': 2026},
            'teams': {'home': {'id': 1118, 'name': 'Netherlands'}, 'away': {'id': 24, 'name': 'Poland'}},
            'goals': {'home': 2, 'away': 1}}
        self.client.get.return_value = [self.row]
        self.client.competition.return_value = {'provider_id': 10, 'name': 'Friendlies', 'country': 'World', 'country_code': '', 'competition_type': 'Cup'}

    def sync(self):
        return sync_team_history([self.team, self.team], client=self.client, as_of=date(2026, 9, 21))

    def test_seasons_dedup_identity_updates_and_existing_importer(self):
        from matches.services.football_sync import upsert_fixture
        with patch('matches.services.team_history.upsert_fixture', wraps=upsert_fixture) as upsert:
            first = self.sync()
            self.assertEqual(upsert.call_count, 1)
        self.assertEqual(first.created, 1)
        requests = self.client.get.call_args_list
        self.assertEqual([call.kwargs['season'] for call in requests], [2024, 2025, 2026])
        self.assertTrue(all(call.kwargs == {'team': 1118, 'season': year, 'from': '2025-09-21', 'to': '2026-09-21'} for call, year in zip(requests, [2024, 2025, 2026])))
        self.assertEqual(self.sync().unchanged, 1)
        self.row['goals']['home'] = 3
        self.assertEqual(self.sync().updated, 1)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(Team.objects.count(), 2)
        self.assertEqual(Match.objects.get().home_team_entity_id, self.team.pk)
        self.assertIsNone(Match.objects.get().home_goals)

    def test_failure_preserves_history_and_does_not_mark_round_synced(self):
        self.sync()
        self.client.get.side_effect = ProviderError('Unavailable')
        round_ = Round.objects.create(name='Failed sync')
        match = Match.objects.get()
        Match.objects.filter(pk=match.pk).update(round=round_)
        report = sync_round_team_history(round_, client=self.client, as_of=date(2026, 9, 21))
        self.assertTrue(report.errors)
        self.assertEqual(Match.objects.count(), 1)
        round_.refresh_from_db()
        self.assertIsNone(round_.team_history_synced_at)

    def test_refresh_imports_new_completion_and_multiple_competitions(self):
        self.sync()
        import copy
        second = copy.deepcopy(self.row)
        second['fixture'] = {'id': 901, 'date': '2026-09-20T18:00:00Z', 'status': {'short': 'FT'}}
        second['league'] = {'id': 1, 'season': 2026}
        self.client.get.return_value = [self.row, second]
        self.client.competition.side_effect = lambda competition, season: {
            'provider_id': competition, 'name': 'World Cup' if competition == 1 else 'Friendlies',
            'country': 'World', 'country_code': '', 'competition_type': 'Cup',
        }
        round_ = Round.objects.create(name='Refresh', is_active=True)
        target = Match.objects.create(round=round_, league='Test', home_team='Netherlands', away_team='Poland',
            home_team_entity=self.team, kickoff=timezone.now()+timedelta(days=2))
        report = sync_round_team_history(round_, client=self.client, as_of=date(2026, 9, 21))
        self.assertEqual((report.created, report.unchanged, report.errors), (1, 1, []))
        round_.refresh_from_db()
        self.assertIsNotNone(round_.team_history_synced_at)
        self.assertEqual(Match.objects.filter(api_football_id__in=[900, 901]).values('competition_season').distinct().count(), 2)
        self.assertEqual(len(forms_for_matches([target])[target.pk]['homeForm']), 2)
