"""API-Football transport and normalization. No ORM or game side effects."""
import json
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from django.conf import settings
from django.utils import timezone


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class FootballFixture:
    provider_id: int
    competition_id: int
    season: int
    home_id: int
    home_name: str
    away_id: int
    away_name: str
    kickoff: datetime
    status: str
    score: dict


def normalize_fixture(item):
    try:
        fixture, league, teams = item["fixture"], item["league"], item["teams"]
        kickoff = datetime.fromisoformat(fixture["date"].replace("Z", "+00:00"))
        ids = [int(fixture["id"]), int(league["id"]), int(league["season"]), int(teams["home"]["id"]), int(teams["away"]["id"])]
        if timezone.is_naive(kickoff) or any(value <= 0 for value in ids) or ids[3] == ids[4]:
            raise ValueError()
        status = fixture["status"]["short"]
        if not isinstance(status, str) or not status:
            raise ValueError()
        return FootballFixture(ids[0], ids[1], ids[2], ids[3], teams["home"]["name"], ids[4], teams["away"]["name"],
            kickoff, status, {"goals": item.get("goals", {}), "periods": item.get("score", {})})
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError("Invalid fixture identity, teams or kickoff in provider response.") from None


class ApiFootballClient:
    def __init__(self, key=None, opener=None):
        self.key = settings.API_FOOTBALL_KEY if key is None else key
        self.opener = opener or urlopen

    def get(self, endpoint, **params):
        if not self.key:
            raise ProviderError("API_FOOTBALL_KEY is not configured.")
        request = Request(f"{settings.API_FOOTBALL_BASE_URL}/{endpoint}?{urlencode(params)}",
            headers={"x-apisports-key": self.key, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=settings.API_FOOTBALL_TIMEOUT) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise ProviderError(f"API-Football HTTP {error.code}; check credentials/quota and retry manually.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("API-Football network or invalid JSON response.") from None
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("response"), list):
            # Never echo provider bodies: they can contain request credentials.
            raise ProviderError("API-Football rejected the request or returned an invalid envelope.")
        paging = payload.get("paging", {})
        if paging.get("total", 1) != 1:
            raise ProviderError("Unexpected paginated response; refusing a partial import.")
        return payload["response"]

    def competition(self, competition, season):
        items = self.get("leagues", id=competition, season=season)
        if len(items) != 1:
            raise ProviderError("Expected exactly one competition for this season.")
        try:
            item = items[0]
            if int(item["league"]["id"]) != competition:
                raise ValueError()
            return {"provider_id": competition, "name": item["league"]["name"],
                "competition_type": item["league"]["type"], "country": item["country"]["name"],
                "country_code": item["country"].get("code") or ""}
        except (KeyError, TypeError, ValueError):
            raise ProviderError("Invalid competition metadata.") from None

    def fixtures(self, competition, season, *, date_from=None, date_to=None, live=None):
        params = {"league": competition, "season": season}
        if date_from is not None:
            params["from"] = str(date_from)
        if date_to is not None:
            params["to"] = str(date_to)
        if live is not None:
            params["live"] = live
        return self.get("fixtures", **params)
