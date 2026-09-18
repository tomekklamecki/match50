"""Reusable, aggregate player statistics for profiles and achievements."""

from dataclasses import asdict, dataclass

from matches.models import ChipAssignment, Match, Prediction, Round
from matches.services.effective_match import resolve_effective_match
from matches.services.scoring import actual_outcome


@dataclass(frozen=True)
class AccuracyStatistics:
    submitted: int
    correct: int
    incorrect: int
    accuracy: float | None

    @classmethod
    def from_counts(cls, submitted, correct):
        return cls(
            submitted=submitted,
            correct=correct,
            incorrect=submitted - correct,
            accuracy=round(correct * 100 / submitted, 2) if submitted else None,
        )


@dataclass(frozen=True)
class PlayerStatistics:
    typy: AccuracyStatistics
    gole: AccuracyStatistics

    def as_dict(self):
        return asdict(self)


def calculate_player_statistics(user):
    """Return all-time statistics from submitted predictions on FINISHED slots.

    A Round slot is resolved through the same effective-match resolver used by
    scoring.  Therefore a persisted SWAP is evaluated against its replacement,
    never against the original Draft winner.
    """
    rounds = list(Round.objects.prefetch_related("matches"))
    assignments = list(
        ChipAssignment.objects.filter(user=user).select_related("replacement_match")
    )
    assignment_by_slot = {(item.round_id, item.match_id): item for item in assignments}

    slots = []
    effective_ids = set()
    for round_ in rounds:
        for original_match in round_.matches.all():
            assignment = assignment_by_slot.get((round_.id, original_match.id))
            effective_match = resolve_effective_match(original_match, assignment)
            if effective_match.status == Match.Status.FINISHED:
                slots.append((effective_match, assignment))
                effective_ids.add(effective_match.id)

    predictions = {
        prediction.match_id: prediction
        for prediction in Prediction.objects.filter(user=user, match_id__in=effective_ids)
    }
    typy_submitted = typy_correct = gole_submitted = gole_correct = 0
    for match, assignment in slots:
        prediction = predictions.get(match.id)
        if not prediction:
            continue
        outcome = actual_outcome(match)
        # FINISHED matches with a malformed score are excluded defensively;
        # normal model validation prevents this state.
        if outcome is None:
            continue
        choices = (
            assignment.outcomes
            if assignment and assignment.chip == ChipAssignment.Chip.DOUBLE_PICK
            else [prediction.predicted_result]
        )
        typy_submitted += 1
        typy_correct += int(outcome in choices)
        if prediction.total_goals is not None:
            gole_submitted += 1
            gole_correct += int(prediction.total_goals == match.home_goals + match.away_goals)

    return PlayerStatistics(
        typy=AccuracyStatistics.from_counts(typy_submitted, typy_correct),
        gole=AccuracyStatistics.from_counts(gole_submitted, gole_correct),
    )
