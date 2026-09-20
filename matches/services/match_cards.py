"""Shared, presentation-ready state for settled MATCH50 match slots."""

from matches.models import ChipAssignment, Match
from matches.services.effective_match import resolve_effective_match


def prepare_settled_match(slot, assignment=None, prediction=None, score_info=None):
    """Decorate one original Round slot using the authoritative effective match.

    The same helper is deliberately used by the Typy page and public history,
    so a SWAP and its settled scoring state cannot drift between the views.
    """
    score_info = score_info or {}
    slot.effective_match = resolve_effective_match(slot, assignment)
    slot.saved_chip = assignment.chip if assignment else ""
    slot.saved_outcomes = assignment.outcomes if assignment and assignment.chip == ChipAssignment.Chip.DOUBLE_PICK else []
    slot.saved_goal_team = assignment.goal_team if assignment else ""
    slot.saved_prediction = prediction.predicted_result if prediction else ""
    slot.saved_goals = "" if not prediction or prediction.total_goals is None else prediction.total_goals
    slot.score_breakdown = score_info
    slot.match_points = score_info.get("typy", 0) + score_info.get("gole", 0) + score_info.get("bonus", 0)

    status = slot.effective_match.status
    if status == Match.Status.CANCELLED:
        slot.standard_score_state = slot.goal_score_state = "neutral"
    elif status == Match.Status.FINISHED:
        slot.standard_score_state = "hit" if score_info.get("standard_correct") is True else "miss" if score_info.get("standard_correct") is False else "neutral"
        slot.goal_score_state = "hit" if score_info.get("goal_correct") is True else "miss" if score_info.get("goal_correct") is False else "neutral"
    else:
        slot.standard_score_state = slot.goal_score_state = "neutral"
    slot.chip_score_state = "neutral"
    if assignment and status == Match.Status.FINISHED:
        if assignment.chip == ChipAssignment.Chip.BANKER:
            banker = score_info.get("banker_bonus", 0)
            slot.chip_score_state = "hit" if banker > 0 else "miss" if banker < 0 else "neutral"
        elif assignment.chip == ChipAssignment.Chip.GOOOOOOOAL:
            slot.chip_score_state = "hit" if score_info.get("goooooooal_bonus", 0) > 0 else "neutral"
        elif assignment.chip == ChipAssignment.Chip.DOUBLE_PICK:
            slot.chip_score_state = slot.standard_score_state
    return slot
