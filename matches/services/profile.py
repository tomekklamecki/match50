from django.core.paginator import Paginator
from math import ceil
from matches.models import ChipAssignment, Match, Match50Season, Prediction, Round, TrophyFinish, UserAchievement, UserRoundScore
from matches.services.match_cards import prepare_settled_match
from matches.services.player_statistics import calculate_player_statistics
from matches.services.rankings import month_ranking, round_ranking, season_ranking


def _is_completed_round(round_):
    matches = list(round_.matches.all())
    return bool(matches) and all(match.status in {Match.Status.FINISHED, Match.Status.CANCELLED} for match in matches)


def public_history(user, page=1, competition=None, result=None, round_id=None):
    """Build history in complete Round-sized groups, never Prediction rows."""
    assignments = {
        (item.round_id, item.match_id): item
        for item in ChipAssignment.objects.filter(user=user).select_related(
            "replacement_match__home_team_entity", "replacement_match__away_team_entity"
        )
    }
    all_candidates = list(
        Round.objects.prefetch_related(
            "matches__competition_season__competition", "matches__home_team_entity", "matches__away_team_entity"
        )
        .order_by("-ranking_date", "-id")
    )
    all_candidates = [round_ for round_ in all_candidates if _is_completed_round(round_)]
    candidates = all_candidates
    if round_id:
        candidates = [round_ for round_ in candidates if str(round_.id) == str(round_id)]

    groups = []
    for round_ in candidates:
        score = UserRoundScore.objects.filter(user=user, round=round_).first()
        score_by_match = {item.get("match", item.get("effective_match")): item for item in score.breakdown} if score else {}
        predictions = {}
        slots = []
        from matches.services.match_order import ordered_matches
        for original in ordered_matches(round_):
            assignment = assignments.get((round_.id, original.id))
            effective = assignment.replacement_match if assignment and assignment.chip == ChipAssignment.Chip.SWAP else original
            if effective.id not in predictions:
                predictions[effective.id] = Prediction.objects.filter(user=user, match=effective).first()
            slot = prepare_settled_match(original, assignment, predictions[effective.id], score_by_match.get(effective.id))
            slots.append(slot)

        # A league filter narrows the displayed matches inside every completed
        # round.  It must not merely select rounds which happen to contain the
        # league, because the profile then still showed unrelated fixtures.
        if competition:
            slots = [
                slot for slot in slots
                if slot.effective_match.competition_season
                and slot.effective_match.competition_season.competition.code == competition
            ]
            if not slots:
                continue
        if result:
            predicted_slots = [slot for slot in slots if slot.saved_prediction]
            if result == "hit" and not any(slot.standard_score_state == "hit" for slot in predicted_slots):
                continue
            if result == "miss" and not any(slot.standard_score_state == "miss" for slot in predicted_slots):
                continue
        ranking = next((entry for entry in round_ranking(round_) if entry.user_id == user.id), None)
        groups.append({
            "round": round_, "score": score, "rank": ranking.rank if ranking else None,
            "slots": slots,
            "starts_at": min(slot.effective_match.kickoff for slot in slots),
            "ends_at": max(slot.effective_match.kickoff for slot in slots),
        })
    # History is reviewed one complete Round at a time.  Filters narrow the
    # displayed match slots but do not change this chronological navigation.
    return Paginator(groups, 1).get_page(page), all_candidates


def round_history_summaries(user):
    """Return the player's classified, completed rounds from newest to oldest."""
    rounds = list(
        Round.objects.filter(user_scores__user=user)
        .prefetch_related("matches")
        .order_by("-ranking_date", "-id")
        .distinct()
    )
    round_medals = {
        trophy.period_key: trophy.rank
        for trophy in TrophyFinish.objects.filter(user=user, scope=TrophyFinish.Scope.ROUND)
    }
    medal_labels = {1: "🥇", 2: "🥈", 3: "🥉"}
    summaries = []
    for round_ in rounds:
        if not _is_completed_round(round_):
            continue
        entries = round_ranking(round_)
        entry = next((item for item in entries if item.user_id == user.id), None)
        if entry is None:
            continue
        finish_position = round_medals.get(str(round_.id))
        medal = medal_labels.get(finish_position, "")
        top_percent = max(1, ceil(entry.rank * 100 / len(entries)))
        summaries.append({
            "round": round_,
            "points": entry.total,
            "rank": entry.rank,
            "classified_count": len(entries),
            "top_percent": top_percent,
            "finish_position": finish_position,
            "medal": medal,
            "result_status": medal or f"TOP {top_percent}%",
        })
    return summaries


def _progress(value, thresholds):
    """Return display state from the player's real metric, not unlocked tiers."""
    current = next((tier for tier, threshold in reversed(thresholds) if value >= threshold), None)
    next_item = next(((tier, threshold) for tier, threshold in thresholds if value < threshold), None)
    target = next_item[1] if next_item else thresholds[-1][1]
    return {
        "tier": current or "—",
        "progress": value,
        "next": next_item,
        "percent": min(100, round((value / target) * 100)) if target else 0,
    }


def _performance_entry(entries, user):
    entry = next((item for item in entries if item.user_id == user.id), None)
    return {"points": entry.total if entry else 0, "rank": entry.rank if entry else None}


def current_performance(user):
    current_round = Round.objects.filter(is_active=True).order_by("id").first()
    reference_date = current_round.ranking_date if current_round else None
    season = current_round.match50_season if current_round else Match50Season.objects.filter(is_active=True).first()
    return {
        "round": _performance_entry(round_ranking(current_round), user) if current_round else {"points": 0, "rank": None},
        "month": _performance_entry(month_ranking(reference_date.year, reference_date.month), user) if reference_date else {"points": 0, "rank": None},
        "season": _performance_entry(season_ranking(season), user) if season else {"points": 0, "rank": None},
    }

def profile_data(user, page=1, competition=None, result=None, round_id=None):
    unlocks=list(UserAchievement.objects.filter(user=user).select_related("achievement").order_by("-unlocked_at"))
    league_codes=[("EPL","Premier League"),("LALIGA","La Liga"),("BUNDESLIGA","Bundesliga"),("SERIEA","Serie A"),("LIGUE1","Ligue 1"),("EKSTRAKLASA","Ekstraklasa"),("UCL","Champions League"),("UEL","Europa League"),("UECL","Conference League")]
    trophy_rows=[]; trophies=TrophyFinish.objects.filter(user=user)
    for scope,label in (("ROUND","KOLEJKI"),("MONTH","MIESIĄCE"),("SEASON","SEZONY")):
        trophy_rows.append({"label":label,"gold":trophies.filter(scope=scope,rank=1).count(),"silver":trophies.filter(scope=scope,rank=2).count(),"bronze":trophies.filter(scope=scope,rank=3).count()})
    from .achievements import TIERS, GOAL_TIERS, REPEAT_TIERS, STREAK_TIERS, MASTERY_TIERS
    states = {item.achievement.code: item for item in unlocks}
    def progress(code, thresholds):
        return _progress(states[code].progress if code in states else 0, thresholds)
    paths = {}
    for key, code, label, thresholds in (
        ("typy", "TYPY", "TYPY", TIERS),
        ("typy_again", "TYPY_AGAIN", "TYPY — DO IT AGAIN", REPEAT_TIERS),
        ("gole", "GOLE", "GOLE", GOAL_TIERS),
        ("gole_again", "GOLE_AGAIN", "GOLE — DO IT AGAIN", REPEAT_TIERS),
        ("streak", "STREAK", "SERIA", STREAK_TIERS),
        ("streak_again", "STREAK_AGAIN", "SERIA — DO IT AGAIN", REPEAT_TIERS),
    ):
        discovered = not key.endswith("_again") or bool(code in states and states[code].context_data.get("discovered"))
        paths[key] = {**progress(code, thresholds), "label": label, "discovered": True} if discovered else {"label": "???????", "discovered": False}
    mastery = [{"code":code, "name":name, "data":progress(f"MASTERY_{code}", MASTERY_TIERS)}
               for code, name in league_codes]
    streak_state = states.get("STREAK")
    streaks = streak_state.context_data if streak_state else {"current_streak":0, "max_streak":0}
    return {"statistics":calculate_player_statistics(user), "streaks":streaks, "performance":current_performance(user), "round_history_preview":round_history_summaries(user)[:5], "achievements":[item for item in unlocks if item.unlocked], "trophies":trophy_rows, "paths":paths, "mastery":mastery}
