"""Small, canonical presentation payload for a team's generic shirt."""
from .league_flags import national_team_flag

FALLBACK_SHIRT = {"primary": "#94a3b8", "secondary": "#e2e8f0", "pattern": "SOLID"}
ALLOWED_PATTERNS = {"SOLID", "VERTICAL_STRIPES", "HORIZONTAL_HOOPS", "CONTRAST_SLEEVES", "HALVES"}


def shirt_config(team):
    if team is None:
        return dict(FALLBACK_SHIRT)
    pattern = team.shirt_pattern if team.shirt_pattern in ALLOWED_PATTERNS else "SOLID"
    return {
        "primary": team.shirt_primary or FALLBACK_SHIRT["primary"],
        "secondary": team.shirt_secondary or FALLBACK_SHIRT["secondary"],
        "pattern": pattern,
    }


def team_visual_config(team):
    """Canonical visual payload: a national flag when resolvable, else a shirt."""
    return {"flag": national_team_flag(team), "shirt": shirt_config(team)}


def match_presentation(match):
    home_visual = team_visual_config(match.home_team_entity)
    away_visual = team_visual_config(match.away_team_entity)
    return {
        "teams": f"{match.home_team} – {match.away_team}",
        "home": match.home_team,
        "away": match.away_team,
        "homeShirt": home_visual["shirt"],
        "awayShirt": away_visual["shirt"],
        "homeFlag": home_visual["flag"],
        "awayFlag": away_visual["flag"],
    }
