from matches.models import ChipAssignment, Match, Prediction, Round
from matches.services.effective_match import resolve_effective_match
from matches.services.scoring import actual_outcome


def typy_streaks(user):
    assignments = {(a.round_id, a.match_id): a for a in ChipAssignment.objects.filter(user=user).select_related("replacement_match")}
    events = []
    for round_ in Round.objects.prefetch_related("matches"):
        for original in round_.matches.all():
            effective = resolve_effective_match(original, assignments.get((round_.id, original.id)))
            if effective.status == Match.Status.FINISHED and effective.finished_at:
                prediction = Prediction.objects.filter(user=user, match=effective).first()
                if prediction:
                    assignment = assignments.get((round_.id, original.id))
                    choices = assignment.outcomes if assignment and assignment.chip == "DOUBLE_PICK" else [prediction.predicted_result]
                    events.append((effective.finished_at, effective.id, actual_outcome(effective) in choices))
    # Same timestamp is evaluated as one batch: any miss resets before hits;
    # this avoids database-ID ordering deciding a player's streak.
    events.sort(key=lambda item: item[0])
    current = maximum = 0
    index = 0
    while index < len(events):
        timestamp = events[index][0]; batch=[]
        while index < len(events) and events[index][0] == timestamp:
            batch.append(events[index][2]); index += 1
        if False in batch: current = 0
        current += sum(batch)
        maximum = max(maximum, current)
    return {"current_streak": current, "max_streak": maximum}
