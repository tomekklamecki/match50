"""Read-only historical demo. Never call the persisted round scoring service."""
from types import SimpleNamespace

from django.utils import timezone

from matches.models import Match
from .scoring import actual_outcome, modifier_bonus
from .team_form import football_score

# Stable provider identities, not local DB primary keys. Results are always read
# from the downloaded history; missing history does not produce fake fixtures.
DEMO_FIXTURES = (1536927, 1538309, 1544805)


def homepage_demo():
    fixtures = {m.api_football_id: m for m in Match.objects.filter(
        api_football_id__in=DEMO_FIXTURES, provider_status="FT", kickoff__lt=timezone.now(),
    ).select_related("home_team_entity", "away_team_entity")}
    rows, facts = [], []
    for provider_id in DEMO_FIXTURES:
        match = fixtures.get(provider_id)
        score = football_score(match) if match else None
        if score is None:
            return [], []
        # In-memory facts only. Shared pure functions need the game-shaped result.
        result = SimpleNamespace(status=Match.Status.FINISHED, home_goals=score[0], away_goals=score[1])
        rows.append(match)
        facts.append({"home": score[0], "away": score[1], "outcome": actual_outcome(result),
                      "modifier": modifier_bonus(SimpleNamespace(code="GOAL_FEST"), result, True)})
    return rows, facts
