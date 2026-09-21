from matches.models import ChipAssignment, Prediction, Round
from matches.services.effective_match import resolve_effective_match
from matches.services.scoring import typ_success
from matches.services.match_order import ordered_matches


def streak_analysis(user):
    """Scoring's normal TYP success in frozen slot order across rounds.

    Unresolved, cancelled and unpredicted slots are skipped, as before.
    SWAP retains its original slot. Effective VAR and DOUBLE PICK selections
    count exactly as in scoring; GOLE and bonus rewards are irrelevant.
    """
    assignments = {(a.round_id, a.match_id): a for a in
                   ChipAssignment.objects.filter(user=user).select_related("replacement_match")}
    predictions = {p.match_id: p for p in Prediction.objects.filter(user=user)}
    current = maximum = 0
    qualifying_runs = []
    start = None
    rounds = Round.objects.filter(frozen_match_order__isnull=False).prefetch_related("matches").order_by("ranking_date", "id")
    for round_ in rounds:
        for original in ordered_matches(round_):
            assignment = assignments.get((round_.id, original.id))
            effective = resolve_effective_match(original, assignment)
            prediction = predictions.get(effective.pk)
            correct = typ_success(effective, prediction, assignment)
            if correct is None:
                continue
            if correct and current == 0:
                start = f"streak:{round_.pk}:{original.pk}"
            current = current + 1 if correct else 0
            if current == 8:
                qualifying_runs.append(start)
            maximum = max(maximum, current)
    return {"current_streak": current, "max_streak": maximum}, qualifying_runs


def typy_streaks(user):
    return streak_analysis(user)[0]
