"""Presentation metadata; eligibility is checked by the existing chip model."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from matches.models import ChipAssignment, Prediction
from .effective_match import prediction_match_ids_for_round, resolve_effective_match
from .league_flags import flag_presentations
from .team_visuals import match_presentation
from .team_form import forms_for_matches


def completion_state(slots):
    values = list(slots.values())
    goals_selected = sum(slot["goals"] != "" for slot in values)
    remaining_goal_slots = sum(slot["goleActionable"] and slot["goals"] == "" for slot in values)
    max_achievable_gole = min(10, goals_selected + remaining_goal_slots)
    return {
        "typyComplete": all(not slot["typyActionable"] or slot["predicted"] for slot in values),
        "goleComplete": goals_selected >= max_achievable_gole,
        "goalsSelected": goals_selected,
        "maxAchievableGole": max_achievable_gole,
    }


def slot_actionability(match, assignment=None, *, authenticated=True):
    prediction_editable = match.predictions_editable or bool(
        assignment
        and assignment.chip == ChipAssignment.Chip.CHANGE_MIND
        and assignment.prediction_editable
    )
    effective = resolve_effective_match(match, assignment)
    typy_actionable = bool(
        authenticated
        and prediction_editable
        and effective.status not in {effective.Status.FINISHED, effective.Status.CANCELLED}
    )
    gole_actionable = bool(typy_actionable and match.predictions_editable)
    return typy_actionable, gole_actionable


def persisted_state(user, round_, matches):
    assignments = list(ChipAssignment.objects.filter(user=user, round=round_).select_related("replacement_match"))
    by_match = {assignment.match_id: assignment for assignment in assignments}
    predictions = {prediction.match_id: prediction for prediction in Prediction.objects.filter(
        user=user, match_id__in=prediction_match_ids_for_round(round_, assignments))}
    slots = {}
    for match in matches:
        assignment = by_match.get(match.id)
        effective = resolve_effective_match(match, assignment)
        prediction = predictions.get(effective.id)
        typy_actionable, gole_actionable = slot_actionability(match, assignment)
        slots[str(match.id)] = {
            "chip": assignment.chip if assignment else "",
            "outcomes": assignment.outcomes if assignment and assignment.chip == ChipAssignment.Chip.DOUBLE_PICK else [prediction.predicted_result] if prediction and prediction.predicted_result else [],
            "goals": prediction.total_goals if prediction and prediction.total_goals is not None else "",
            "predicted": bool(prediction and prediction.predicted_result), "league": effective.league,
            "typyActionable": typy_actionable,
            "goleActionable": gole_actionable,
            **match_presentation(effective),
        }
    return slots


def presentation_state(user, round_, matches):
    assignments = {
        assignment.match_id: assignment
        for assignment in ChipAssignment.objects.filter(user=user, round=round_).select_related("replacement_match")
    } if user.is_authenticated else {}
    forms = forms_for_matches([fixture for match in matches for fixture in (match, match.swap_candidate)])
    def presentation(match):
        return {**match_presentation(match), **forms.get(match.pk, {})}

    slots = {}
    for match in matches:
        assignment = assignments.get(match.id)
        typy_actionable, gole_actionable = slot_actionability(
            match, assignment, authenticated=user.is_authenticated
        )
        chips = {}
        for chip, label in ChipAssignment.Chip.choices:
            reason = ""
            if not user.is_authenticated or not match.predictions_editable:
                reason = "Chipy są zablokowane."
            else:
                probe = ChipAssignment(
                    user=user, round=round_, match=match, chip=chip,
                    outcomes=["1", "X"], goal_team=match.home_team,
                    replacement_match=match.swap_candidate,
                )
                try:
                    probe.clean()
                except ValidationError as error:
                    reason = error.messages[0]
            chips[chip] = {
                "label": label, "reason": reason,
                "lockReason": "Chipy są zablokowane." if not user.is_authenticated or not match.predictions_editable else "",
                "limit": 2 if chip == ChipAssignment.Chip.DOUBLE_PICK else 1,
                "allowed": ["1", "2"] if chip == ChipAssignment.Chip.GOOOOOOOAL else ["1", "X", "2"],
                "requiresPick": chip in {ChipAssignment.Chip.BANKER, ChipAssignment.Chip.GOOOOOOOAL},
                "replacement": chip == ChipAssignment.Chip.SWAP,
            }
        slots[str(match.id)] = {
            "chips": chips, "chip": match.saved_chip,
            "predicted": bool(match.saved_prediction),
            "goals": match.saved_goals,
            "typyActionable": typy_actionable,
            "goleActionable": gole_actionable,
            "league": match.effective_match.league,
            "teams": f"{match.effective_match.home_team} – {match.effective_match.away_team}",
            "original": {**presentation(match), "league": match.league, "kickoff": timezone.localtime(match.kickoff).strftime("%d.%m.%Y · %H:%M")},
            "replacement": {**presentation(match.swap_candidate), "league": match.swap_candidate.league, "kickoff": timezone.localtime(match.swap_candidate.kickoff).strftime("%d.%m.%Y · %H:%M")} if match.swap_candidate else None,
        }
    return {
        "round": round_.id,
        "total": round_.match_count,
        "future": round_.is_future_preview,
        "slots": slots,
        "completion": completion_state(slots),
        "leagueFlags": flag_presentations(),
    }
