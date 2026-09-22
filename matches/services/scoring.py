from django.contrib.auth import get_user_model
from django.db import transaction

from matches.models import ChipAssignment, Match, Prediction, UserRoundScore
from matches.services.effective_match import resolve_effective_match


def actual_outcome(match):
    if match.status != Match.Status.FINISHED or match.home_goals is None or match.away_goals is None:
        return None
    return "1" if match.home_goals > match.away_goals else "2" if match.home_goals < match.away_goals else "X"


def typ_choices(prediction, chip=None):
    return chip.outcomes if chip and chip.chip == "DOUBLE_PICK" else ([prediction.predicted_result] if prediction and prediction.predicted_result else [])


def typ_success(match, prediction, chip=None):
    """Authoritative normal TYP success; None means not evaluable."""
    outcome = actual_outcome(match)
    choices = typ_choices(prediction, chip)
    return outcome in choices if outcome is not None and choices else None


def modifier_bonus(modifier, match, correct):
    if not correct or not modifier:
        return 0
    code = modifier.code
    if code == "HER_MAJESTY_EPL":
        return int(bool(match.competition_season and match.competition_season.competition.code == "EPL"))
    if code == "GOAL_FEST": return int(match.home_goals + match.away_goals >= 4)
    if code == "CLEAN_SHEET": return int(match.home_goals != match.away_goals and min(match.home_goals, match.away_goals) == 0)
    if code == "ALL_HAIL_KING":
        season = match.competition_season
        return int(bool(season and season.champion_team and season.champion_team_id in {match.home_team_entity_id, match.away_team_entity_id}))
    return 0


def recalculate_user_round_score(user, round_):
    from matches.services.match_order import freeze_match_order
    from matches.services.lifecycle import is_completed_round
    if round_.is_active or is_completed_round(round_):
        freeze_match_order(round_)
    matches = list(round_.matches.filter(status__in=[Match.Status.FINISHED, Match.Status.CANCELLED]).select_related("competition_season__competition", "competition_season__champion_team"))
    chips = {chip.match_id: chip for chip in ChipAssignment.objects.filter(user=user, round=round_).select_related("replacement_match")}
    predictions = {item.match_id: item for item in Prediction.objects.filter(user=user)}
    typy = gole = bonus = 0; breakdown=[]
    for original in matches:
        chip = chips.get(original.id)
        # A round slot always scores one effective match.  With SWAP it is the
        # persisted Draft loser; the original remains only as an audit trail.
        match = resolve_effective_match(original, chip)
        prediction = predictions.get(match.id)
        choices = typ_choices(prediction, chip)
        item = {
            "match": match.id,
            "original_match": original.id,
            "effective_match": match.id,
            "status": match.status,
            "chip": chip.chip if chip else "",
            "chip_outcomes": choices if chip and chip.chip == "DOUBLE_PICK" else [],
            "goal_team": chip.goal_team if chip and chip.chip == "GOOOOOOOOAL" else "",
            "standard_prediction": choices,
            "goal_prediction": prediction.total_goals if prediction and prediction.total_goals is not None else None,
            "swap": bool(chip and chip.chip == "SWAP"),
        }
        if match.status == Match.Status.CANCELLED:
            item.update({"cancelled": True, "typy": 0, "gole": 0, "bonus": 0,
                         "standard_correct": None, "goal_correct": None,
                         "banker_bonus": 0, "goooooooal_bonus": 0, "modifier_bonus": 0})
            breakdown.append(item)
            continue
        outcome=actual_outcome(match)
        if not outcome:
            continue
        correct=bool(typ_success(match, prediction, chip)); t=int(correct); g=int(bool(prediction and prediction.total_goals==match.home_goals+match.away_goals))
        modifier = modifier_bonus(round_.active_global_modifier,match,correct)
        banker = (2 if correct else -1) if chip and chip.chip == "BANKER" else 0
        goooooooal = (match.home_goals if chip.goal_team==match.home_team else match.away_goals if chip.goal_team==match.away_team else 0)//2 if chip and chip.chip == "GOOOOOOOOAL" else 0
        b = modifier + banker + goooooooal
        typy+=t; gole+=g; bonus+=b
        item.update({
            "typy": t, "gole": g, "bonus": b,
            "standard_correct": correct if choices else None,
            "goal_correct": bool(g) if prediction and prediction.total_goals is not None else None,
            "banker_bonus": banker, "goooooooal_bonus": goooooooal,
            "modifier_bonus": modifier,
        })
        breakdown.append(item)
    with transaction.atomic():
        score,_=UserRoundScore.objects.update_or_create(user=user,round=round_,defaults={"typy_points":typy,"gole_points":gole,"bonus_points":bonus,"total_points":typy+gole+bonus,"breakdown":breakdown})
    from matches.services.achievements import evaluate_score
    evaluate_score(score)
    return score


@transaction.atomic
def recalculate_round_scores(round_):
    # A SWAP prediction belongs to the persisted replacement match, which is
    # deliberately outside the global Round.  Include chip owners as well so a
    # player who only has a replacement prediction is never skipped.
    user_ids = set(Prediction.objects.filter(match__round=round_).values_list("user_id", flat=True))
    user_ids.update(ChipAssignment.objects.filter(round=round_).values_list("user_id", flat=True))
    users = get_user_model().objects.filter(pk__in=user_ids)
    scores = [recalculate_user_round_score(user,round_) for user in users]
    from matches.services.achievements import evaluate_trophies
    if all(len(score.breakdown) == round_.match_count for score in scores):
        evaluate_trophies(round_)
    return scores
