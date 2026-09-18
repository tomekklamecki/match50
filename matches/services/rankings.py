"""Reusable rankings derived exclusively from persisted UserRoundScore rows."""

from dataclasses import dataclass
from django.db.models import QuerySet

from matches.models import UserRoundScore


@dataclass(frozen=True)
class RankingEntry:
    user_id: int
    username: str
    typy: int
    gole: int
    bonusy: int
    total: int
    rank: int


def _rank(scores: QuerySet):
    totals = {}
    usernames = {}
    for score in scores.select_related("user"):
        item = totals.setdefault(score.user_id, [0, 0, 0, 0])
        item[0] += score.typy_points
        item[1] += score.gole_points
        item[2] += score.bonus_points
        item[3] += score.total_points
        usernames[score.user_id] = score.user.username
    ordered = sorted(totals.items(), key=lambda item: (-item[1][3], -item[1][0], -item[1][1], usernames[item[0]].lower()))
    entries, previous_key, rank = [], None, 0
    for position, (user_id, values) in enumerate(ordered, start=1):
        key = (values[3], values[0], values[1])
        if key != previous_key:
            rank = position
            previous_key = key
        entries.append(RankingEntry(user_id, usernames[user_id], values[0], values[1], values[2], values[3], rank))
    return entries


def round_ranking(round_):
    return _rank(UserRoundScore.objects.filter(round=round_)) if round_ else []


def month_ranking(year, month):
    return _rank(UserRoundScore.objects.filter(round__ranking_date__year=year, round__ranking_date__month=month))


def season_ranking(season):
    return _rank(UserRoundScore.objects.filter(round__match50_season=season)) if season else []


def top_with_current(entries, user=None, limit=100):
    top = entries[:limit]
    current = next((entry for entry in entries if user and entry.user_id == user.id), None)
    return top, current if current and current not in top else None
