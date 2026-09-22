"""Authoritative resolution of a user's effective match in a Round slot."""

from matches.models import ChipAssignment, MatchAlternative


def draft_loser_for_winner(original_match):
    relationship = MatchAlternative.objects.filter(match=original_match).select_related("alternative").first()
    return relationship.alternative if relationship else None


def resolve_effective_match(original_match, assignment=None, replacement_match=None):
    """Return the only match that may be predicted and scored for this slot."""
    if replacement_match is not None:
        return replacement_match
    if assignment and assignment.chip == ChipAssignment.Chip.SWAP and assignment.replacement_match_id:
        return assignment.replacement_match
    return original_match


def prediction_match_ids_for_round(round_, assignments=()):
    """Original Round matches plus persisted effective SWAP replacements."""
    ids = set(round_.matches.values_list("id", flat=True))
    ids.update(
        assignment.replacement_match_id
        for assignment in assignments
        if assignment.chip == ChipAssignment.Chip.SWAP and assignment.replacement_match_id
    )
    return ids
