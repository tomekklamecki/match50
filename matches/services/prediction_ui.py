"""Presentation metadata; eligibility is checked by the existing chip model."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from matches.models import ChipAssignment, Prediction
from .effective_match import prediction_match_ids_for_round, resolve_effective_match
from .league_flags import flag_presentations


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
        slots[str(match.id)] = {
            "chip": assignment.chip if assignment else "",
            "outcomes": assignment.outcomes if assignment and assignment.chip == ChipAssignment.Chip.DOUBLE_PICK else [prediction.predicted_result] if prediction else [],
            "goals": prediction.total_goals if prediction and prediction.total_goals is not None else "",
            "predicted": bool(prediction), "league": effective.league,
            "teams": f"{effective.home_team} – {effective.away_team}",
        }
    return slots


def presentation_state(user, round_, matches):
    slots = {}
    for match in matches:
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
            "league": match.effective_match.league,
            "teams": f"{match.effective_match.home_team} – {match.effective_match.away_team}",
            "original": {"teams": f"{match.home_team} – {match.away_team}", "league": match.league, "kickoff": timezone.localtime(match.kickoff).strftime("%d.%m.%Y · %H:%M")},
            "replacement": {"teams": f"{match.swap_candidate.home_team} – {match.swap_candidate.away_team}", "league": match.swap_candidate.league, "kickoff": timezone.localtime(match.swap_candidate.kickoff).strftime("%d.%m.%Y · %H:%M")} if match.swap_candidate else None,
        }
    return {"round": round_.id, "total": round_.match_count, "future": round_.is_future_preview, "slots": slots, "leagueFlags": flag_presentations()}
