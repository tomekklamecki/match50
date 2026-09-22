"""Local-only score interpretation and batched last-five presentation."""
from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from matches.models import Match


def football_score(match):
    if match.api_football_id:
        if match.provider_status not in {'FT', 'AET', 'PEN'}:
            return None
        score = match.provider_score or {}
        periods = score.get('periods') or {}
        # Shootout tallies are deliberately never read. Extra-time score is
        # preferred for AET/PEN; goals holds the final football score otherwise.
        candidates = ([periods.get('extratime'), score.get('goals'), periods.get('fulltime')]
                      if match.provider_status in {'AET', 'PEN'}
                      else [score.get('goals'), periods.get('fulltime')])
        for candidate in candidates:
            if isinstance(candidate, dict):
                values = candidate.get('home'), candidate.get('away')
                if all(type(value) is int and value >= 0 for value in values):
                    return values
        return None
    if match.status == Match.Status.FINISHED and match.home_goals is not None and match.away_goals is not None:
        return match.home_goals, match.away_goals
    return None


def forms_for_matches(fixtures):
    fixtures = list({match.pk: match for match in fixtures if match is not None}.values())
    if not fixtures:
        return {}
    team_ids = {pk for match in fixtures for pk in (match.home_team_entity_id, match.away_team_entity_id) if pk}
    history = defaultdict(list)
    cutoff = min(max(match.kickoff for match in fixtures), timezone.now())
    rows = Match.objects.filter(
        Q(home_team_entity_id__in=team_ids) | Q(away_team_entity_id__in=team_ids),
        Q(provider_status__in=['FT', 'AET', 'PEN']) | Q(api_football_id__isnull=True, status=Match.Status.FINISHED),
        kickoff__lt=cutoff,
    ).select_related('home_team_entity', 'away_team_entity').order_by('-kickoff', '-pk')
    for match in rows:
        score = football_score(match)
        if score is None:
            continue
        home, away = score
        home_name = match.home_team_entity.name if match.home_team_entity_id else match.home_team
        away_name = match.away_team_entity.name if match.away_team_entity_id else match.away_team
        tooltip = f'{home_name} – {away_name} {home}:{away}'
        for team_id, own, opponent in ((match.home_team_entity_id, home, away), (match.away_team_entity_id, away, home)):
            if team_id in team_ids:
                history[team_id].append((match.kickoff, {
                    'result': 'W' if own > opponent else 'L' if own < opponent else 'D',
                    'tooltip': tooltip,
                }))

    def last_five(team_id, kickoff):
        values = [value for instant, value in history[team_id] if instant < kickoff][:5]
        return list(reversed(values))

    return {match.pk: {
        'homeForm': last_five(match.home_team_entity_id, match.kickoff),
        'awayForm': last_five(match.away_team_entity_id, match.kickoff),
    } for match in fixtures}
