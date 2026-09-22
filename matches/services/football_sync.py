"""Explicit provider-fact writes. Never invoke Match.save's scoring hook."""
from dataclasses import dataclass, field
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from matches.models import Competition, CompetitionSeason, Team, Match
from matches.services.api_football import ApiFootballClient, ProviderError, normalize_fixture
from matches.services.calendar_week import CalendarWeek


@dataclass
class SyncReport:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list = field(default_factory=list)


def persist(obj, fields):
    changed = [key for key, value in fields.items() if getattr(obj, key) != value]
    for key, value in fields.items():
        setattr(obj, key, value)
    obj.full_clean()
    if obj.pk is None:
        obj.save()
    elif changed:
        obj.save(update_fields=changed)
    return obj


@transaction.atomic
def upsert_competition(data, season):
    competition = Competition.objects.select_for_update().filter(api_football_id=data["provider_id"]).first()
    if competition is None:
        candidates = Competition.objects.filter(api_football_id__isnull=True, name=data["name"], country=data["country"])
        if candidates.count() > 1:
            raise ProviderError("Ambiguous existing competition; bind provider ID explicitly in admin.")
        competition = candidates.first() or Competition(code=f"AF{data['provider_id']}")
    competition = persist(competition, {"api_football_id": data["provider_id"], **{key: data[key] for key in ("name", "country", "country_code", "competition_type")}})
    competition_season = CompetitionSeason.objects.filter(competition=competition, provider_season=season).first()
    if competition_season is None:
        competition_season = CompetitionSeason.objects.filter(competition=competition, season_label=str(season), provider_season__isnull=True).first()
        competition_season = competition_season or CompetitionSeason(competition=competition, season_label=str(season))
    return persist(competition_season, {"provider_season": season})


def upsert_team(provider_id, name):
    team = Team.objects.select_for_update().filter(api_football_id=provider_id).first()
    if team is None:
        team = Team.objects.filter(name=name, api_football_id__isnull=True).first() or Team()
    # Existing same-name records bound to another provider ID produce a
    # validation error, never a silent identity merge or name suffix.
    return persist(team, {"api_football_id": provider_id, "name": name})


@transaction.atomic
def upsert_fixture(fixture, competition_season):
    if fixture.competition_id != competition_season.competition.api_football_id or fixture.season != competition_season.provider_season:
        raise ProviderError("Fixture does not belong to the requested competition/season.")
    home = upsert_team(fixture.home_id, fixture.home_name)
    away = upsert_team(fixture.away_id, fixture.away_name)
    match = Match.objects.select_for_update().filter(api_football_id=fixture.provider_id).first()
    created = match is None
    if created:
        match = Match(api_football_id=fixture.provider_id)
    elif (match.home_team_entity_id, match.away_team_entity_id, match.competition_season_id) != (home.pk, away.pk, competition_season.pk):
        raise ProviderError("Fixture identity changed; manual review required.")
    week = CalendarWeek.at(fixture.kickoff)
    facts = {"competition_season_id": competition_season.pk, "home_team_entity_id": home.pk, "away_team_entity_id": away.pk,
        "league": competition_season.competition.name, "home_team": home.name, "away_team": away.name,
        "kickoff": fixture.kickoff, "provider_status": fixture.status, "provider_score": fixture.score,
        "automatic_kw_year": week.year, "automatic_kw_week": week.week}
    if not created and (match.round_id or match.draft_candidate_memberships.exists() or match.alternative_for.exists() or match.prediction_set.exists()):
        # Legacy chip goal_team values compare these display snapshots. Keep
        # them stable in game; canonical Team/Competition names still refresh.
        for key in ("home_team", "away_team", "league"):
            facts.pop(key)
    changed = any(getattr(match, key) != value for key, value in facts.items())
    for key, value in facts.items():
        setattr(match, key, value)
    match.full_clean()
    if created:
        match.provider_synced_at = timezone.now()
        Match.objects.bulk_create([match])
        return "created"
    Match.objects.filter(pk=match.pk).update(**facts, provider_synced_at=timezone.now())
    return "updated" if changed else "unchanged"


def sync_fixtures(competition, season, *, client=None, date_from=None, date_to=None):
    client = client or ApiFootballClient()
    metadata = client.competition(competition, season)
    raw = client.fixtures(competition, season, date_from=date_from, date_to=date_to)
    report = SyncReport(fetched=len(raw))
    competition_season = upsert_competition(metadata, season)
    for index, item in enumerate(raw):
        try:
            result = upsert_fixture(normalize_fixture(item), competition_season)
            setattr(report, result, getattr(report, result)+1)
        except (ProviderError, ValidationError, IntegrityError):
            report.errors.append(f"Fixture item {index + 1}: invalid data or identity conflict; review provider data/admin bindings.")
    return report
