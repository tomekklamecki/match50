"""Explicit, retryable history synchronization; never called by prediction views."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from matches.models import Match, MatchAlternative, Round, Team
from .api_football import ApiFootballClient, ProviderError, normalize_fixture
from .football_sync import SyncReport, upsert_competition, upsert_fixture


def fixtures_for_round_history(round_):
    # Include persisted alternatives even after kickoff: their teams still need
    # refreshes and form on historical/locked cards.
    alternatives = MatchAlternative.objects.filter(match__round=round_, alternative__round__isnull=True)
    return Match.objects.filter(Q(round=round_) | Q(pk__in=alternatives.values('alternative_id')))


def teams_for_round_history(round_):
    fixtures = fixtures_for_round_history(round_)
    return Team.objects.filter(
        Q(pk__in=fixtures.values('home_team_entity_id')) |
        Q(pk__in=fixtures.values('away_team_entity_id'))
    ).distinct().order_by('pk')


def request_round_history(round_):
    round_.team_history_requested_at = timezone.now()
    Round.objects.filter(pk=round_.pk).update(team_history_requested_at=round_.team_history_requested_at)


def history_seasons(start, end):
    # Provider seasons denote their starting year. Include the preceding year
    # for cross-year club seasons, in addition to calendar-year competitions.
    return range(start.year - 1, end.year + 1)


def sync_team_history(teams, *, client=None, as_of=None):
    client = client or ApiFootballClient()
    end = as_of or timezone.now().astimezone(ZoneInfo(settings.FOOTBALL_TIME_ZONE)).date()
    start = end - timedelta(days=365)
    report = SyncReport()
    seen = set()
    seasons = {}
    provider_ids = sorted({team.api_football_id for team in teams if team.api_football_id})
    for provider_id in provider_ids:
        for season in history_seasons(start, end):
            try:
                rows = client.get('fixtures', team=provider_id, season=season,
                                  **{'from': start.isoformat(), 'to': end.isoformat()})
            except ProviderError:
                report.errors.append(f'Team {provider_id}, season {season}: provider request failed; retry the command.')
                continue
            report.fetched += len(rows)
            for index, row in enumerate(rows):
                try:
                    fixture = normalize_fixture(row)
                    if provider_id not in (fixture.home_id, fixture.away_id):
                        raise ProviderError('Wrong team in response.')
                    day = fixture.kickoff.astimezone(ZoneInfo(settings.FOOTBALL_TIME_ZONE)).date()
                    if not start <= day <= end:
                        continue
                    if fixture.provider_id in seen:
                        continue
                    key = (fixture.competition_id, fixture.season)
                    if key not in seasons:
                        metadata = client.competition(*key)
                        seasons[key] = upsert_competition(metadata, fixture.season)
                    outcome = upsert_fixture(fixture, seasons[key])
                    setattr(report, outcome, getattr(report, outcome) + 1)
                    seen.add(fixture.provider_id)
                except (ProviderError, ValidationError, IntegrityError):
                    report.errors.append(f'Team {provider_id}, season {season}, item {index + 1}: invalid data or identity conflict.')
    return report


def sync_round_team_history(round_, **kwargs):
    started = timezone.now()
    report = sync_team_history(teams_for_round_history(round_), **kwargs)
    if not report.errors:
        Round.objects.filter(pk=round_.pk).update(team_history_synced_at=started)
        round_.team_history_synced_at = started
    return report
