from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import ChipAssignment, Draft, DraftModifierVote, DraftPair, DraftVote, GlobalModifier, Prediction, Round, UserRoundScore


def home(request):
    active_round = Round.objects.filter(is_active=True).order_by("id").first()
    return render(request, "matches/home.html", {"active_round": active_round})


def placeholder(request, section):
    labels = {
        "draft": "DRAFT", "rankings": "RANKINGS", "stats": "STATS", "rules": "RULES",
    }
    return render(request, "matches/placeholder.html", {"section": labels[section]})


def draft(request):
    active_draft = Draft.objects.filter(is_active=True).first()
    if active_draft is None:
        return render(request, "matches/draft.html", {"draft": None, "pairs": []})
    with transaction.atomic():
        active_draft.resolve_closed_pairs()
    if not active_draft.is_active:
        return redirect("draft")
    current_day = min(max((timezone.now() - active_draft.starts_at).days + 1, 1), 6)
    pairs = list(active_draft.pairs.filter(day_number=current_day).select_related("match_a", "match_b", "winner"))
    user_votes = {}
    if request.user.is_authenticated:
        user_votes = dict(DraftVote.objects.filter(user=request.user, pair__in=pairs).values_list("pair_id", "selected_match_id"))
    for pair in pairs:
        pair.user_selected_match_id = user_votes.get(pair.id)
        pair.user_has_voted = pair.user_selected_match_id is not None
        if pair.user_has_voted or pair.resolved_at:
            percentages = pair.percentages()
            pair.match_a_percentage = percentages[pair.match_a_id]
            pair.match_b_percentage = percentages[pair.match_b_id]
    completed_votes = 0
    if request.user.is_authenticated:
        completed_votes = DraftVote.objects.filter(user=request.user, pair__draft=active_draft).count()
    modifier_options = list(active_draft.modifier_options.all())
    modifier_vote = None
    if request.user.is_authenticated:
        modifier_vote = DraftModifierVote.objects.filter(user=request.user, draft=active_draft).first()
    show_modifier_results = bool(active_draft.winning_modifier_id or modifier_vote)
    if show_modifier_results:
        total_modifier_votes = active_draft.modifier_votes.count()
        for option in modifier_options:
            option.vote_percentage = round(option.draftmodifiervote_set.filter(draft=active_draft).count() * 100 / total_modifier_votes) if total_modifier_votes else 0
    return render(request, "matches/draft.html", {
        "draft": active_draft,
        "pairs": pairs,
        "current_day": current_day,
        "day_closes_at": active_draft.starts_at + timedelta(days=current_day),
        "completed_votes": completed_votes,
        "modifier_options": modifier_options,
        "modifier_vote": modifier_vote,
        "show_modifier_results": show_modifier_results,
    })


@require_POST
def draft_vote(request, pair_id):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication is required to vote.")
    pair = get_object_or_404(DraftPair.objects.select_related("draft"), pk=pair_id, draft__is_active=True)
    try:
        selected_match_id = int(request.POST.get("selected_match", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid selected match.")
    try:
        with transaction.atomic():
            DraftVote.objects.create(user=request.user, pair=pair, selected_match_id=selected_match_id)
    except (IntegrityError, ValidationError):
        return HttpResponseBadRequest("This vote cannot be submitted.")
    return redirect("draft")


@require_POST
def draft_modifier_vote(request, modifier_id):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication is required to vote.")
    draft_ = get_object_or_404(Draft, is_active=True)
    modifier = get_object_or_404(GlobalModifier, pk=modifier_id)
    try:
        DraftModifierVote.objects.create(user=request.user, draft=draft_, selected_modifier=modifier)
    except (IntegrityError, ValidationError):
        return HttpResponseBadRequest("This modifier vote cannot be submitted.")
    return redirect("draft")


def typy(request):
    active_round = get_object_or_404(Round, is_active=True)
    matches = list(active_round.matches.order_by("kickoff", "id"))
    existing = {}
    assignments = []
    if request.user.is_authenticated:
        existing = {item.match_id: item for item in Prediction.objects.filter(user=request.user)}
        assignments = list(ChipAssignment.objects.filter(user=request.user, round=active_round).select_related("replacement_match"))
    assignment_by_match = {item.match_id: item for item in assignments}
    for match in matches:
        assignment = assignment_by_match.get(match.id)
        match.saved_chip = assignment.chip if assignment else ""
        match.saved_outcomes = assignment.outcomes if assignment and assignment.chip == ChipAssignment.Chip.DOUBLE_PICK else []
        match.saved_goal_team = assignment.goal_team if assignment else ""
        match.effective_match = assignment.replacement_match if assignment and assignment.chip == ChipAssignment.Chip.SWAP else match
        pair = DraftPair.objects.filter(winner=match, resolved_at__isnull=False).select_related("match_a", "match_b").first()
        match.swap_candidate = (pair.match_b if pair and pair.match_a_id == match.id else pair.match_a if pair else None)
        match.chip_prediction_editable = match.predictions_editable or (assignment and assignment.chip == ChipAssignment.Chip.CHANGE_MIND and assignment.prediction_editable)
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect("login")
        goal_match_ids = {item.match_id for item in existing.values() if item.total_goals is not None}
        changes = []
        deletions = []
        for match in matches:
            results = request.POST.getlist(f"result_{match.id}")
            result = results[0] if results else ""
            total_goals = request.POST.get(f"goals_{match.id}", "")
            saved_mind = assignment_by_match.get(match.id)
            prediction_editable = match.predictions_editable or (saved_mind and saved_mind.chip == ChipAssignment.Chip.CHANGE_MIND and saved_mind.prediction_editable)
            if not prediction_editable:
                if result or total_goals:
                    return HttpResponseBadRequest("Predictions for matches at or after kickoff cannot be edited.")
                continue
            if any(item not in {"1", "X", "2"} for item in results):
                return HttpResponseBadRequest("Invalid standard prediction.")
            if total_goals:
                try:
                    total_goals = int(total_goals)
                except ValueError:
                    return HttpResponseBadRequest("Invalid goal prediction.")
                if not 0 <= total_goals <= 15:
                    return HttpResponseBadRequest("Goal predictions must be between 0 and 15.")
            else:
                total_goals = None
            # Resolve the submitted SWAP now, not only after ChipAssignment is
            # saved.  This makes the replacement the prediction target in the
            # same explicit-save request that activates SWAP.
            requested_chip = request.POST.get(f"chip_{match.id}", "")
            target_match = match
            if requested_chip == ChipAssignment.Chip.SWAP:
                pair = DraftPair.objects.filter(winner=match, resolved_at__isnull=False).first()
                if pair:
                    target_match = pair.match_b if pair.match_a_id == match.id else pair.match_a
            if result:
                changes.append((target_match, result, total_goals))
                if total_goals is None:
                    goal_match_ids.discard(target_match.id)
                else:
                    goal_match_ids.add(target_match.id)
            elif total_goals is not None:
                return HttpResponseBadRequest("Select a standard prediction before adding a goal prediction.")
            elif target_match.id in existing:
                deletions.append(target_match.id)
                goal_match_ids.discard(target_match.id)
        goal_count = len(goal_match_ids)
        if goal_count > 10:
            return HttpResponseBadRequest("A maximum of 10 goal predictions is allowed per round.")
        if goal_count < 10 and request.POST.get("confirm_less_than_ten") != "1":
            messages.warning(request, f"Nie wybrałeś 10 meczów do określenia liczby goli. Obecnie masz {goal_count}/10. Jeśli zapiszesz teraz, nie zdobędziesz punktów za pozostałe predykcje.")
            return render_typy(request, active_round, matches, existing, request.POST, True)
        if any(not match.predictions_editable for match, _, _ in changes):
            return HttpResponseBadRequest("Predictions for matches at or after kickoff cannot be edited.")
        desired_chips = []
        for match in matches:
            chip = request.POST.get(f"chip_{match.id}", "")
            if chip and chip not in ChipAssignment.Chip.values:
                return HttpResponseBadRequest("Invalid chip.")
            current = assignment_by_match.get(match.id)
            if not match.predictions_editable:
                if current:
                    chip, outcomes, goal_team, replacement = current.chip, current.outcomes, current.goal_team, current.replacement_match
                    desired_chips.append((match, chip, outcomes, goal_team, replacement))
                    continue
                if chip:
                    return HttpResponseBadRequest("Chip assignments lock at kickoff.")
            if not chip:
                continue
            outcomes = request.POST.getlist(f"result_{match.id}") if chip == ChipAssignment.Chip.DOUBLE_PICK else []
            goal_team = ""
            if chip == ChipAssignment.Chip.GOOOOOOOAL:
                selected = request.POST.getlist(f"result_{match.id}")
                if selected == ["1"]:
                    goal_team = match.home_team
                elif selected == ["2"]:
                    goal_team = match.away_team
                else:
                    return render_typy(request, active_round, matches, existing, request.POST, chip_error="GOOOOOOOOAL! wymaga typu 1 albo 2 — remis nie wybiera drużyny.")
            replacement = None
            if chip == ChipAssignment.Chip.SWAP:
                pair = DraftPair.objects.filter(winner=match, resolved_at__isnull=False).first()
                replacement = pair.match_b if pair and pair.match_a_id == match.id else (pair.match_a if pair else None)
            desired_chips.append((match, chip, outcomes, goal_team, replacement))
        if len(desired_chips) != len({chip for _, chip, _, _, _ in desired_chips}):
            return HttpResponseBadRequest("Ten chip jest już w użyciu. Usuń go z poprzedniego meczu, aby użyć go tutaj.")
        if len(desired_chips) != len({match.id for match, *_ in desired_chips}):
            return HttpResponseBadRequest("Only one chip can be assigned to a match.")
        for match, chip, outcomes, _, _ in desired_chips:
            if chip == ChipAssignment.Chip.BANKER and not request.POST.getlist(f"result_{match.id}"):
                return HttpResponseBadRequest("BANKER wymaga typu 1/X/2.")
            if chip == ChipAssignment.Chip.DOUBLE_PICK and set(outcomes) not in ({"1", "X"}, {"1", "2"}, {"X", "2"}):
                return render_typy(request, active_round, matches, existing, request.POST, chip_error="DOUBLE PICK wymaga dokładnie dwóch różnych wyników.")
        try:
            with transaction.atomic():
                ChipAssignment.objects.filter(user=request.user, round=active_round).delete()
                for match, chip, outcomes, goal_team, replacement in desired_chips:
                    ChipAssignment.objects.create(user=request.user, round=active_round, chip=chip, match=match, outcomes=outcomes, goal_team=goal_team, replacement_match=replacement)
            Prediction.objects.filter(user=request.user, match_id__in=deletions).delete()
            for match, result, total_goals in changes:
                Prediction.objects.update_or_create(user=request.user, match=match, defaults={"predicted_result": result, "total_goals": total_goals})
        except ValidationError as error:
            return render_typy(request, active_round, matches, existing, request.POST, chip_error=error.messages[0])
        messages.success(request, "Typy zostały zapisane.")
        return redirect("typy")
    return render_typy(request, active_round, matches, existing)


def render_typy(request, active_round, matches, existing, form_data=None, needs_confirmation=False, chip_error=None):
    for match in matches:
        prediction = existing.get(match.effective_match.id)
        match.saved_prediction = prediction.predicted_result if prediction else ""
        match.saved_goals = "" if not prediction or prediction.total_goals is None else prediction.total_goals
        if form_data is not None:
            match.saved_prediction = form_data.get(f"result_{match.id}", match.saved_prediction)
            match.saved_goals = form_data.get(f"goals_{match.id}", match.saved_goals)
            match.saved_chip = form_data.get(f"chip_{match.id}", match.saved_chip)
            match.saved_outcomes = form_data.getlist(f"result_{match.id}") if match.saved_chip == ChipAssignment.Chip.DOUBLE_PICK else []
            match.saved_goal_team = form_data.get(f"goal_team_{match.id}", match.saved_goal_team)
    chip_options = [
        ("BANKER", "B", "Trafiony typ daje +2 bonusu. Pudło: -1."),
        ("DOUBLE_PICK", "2X", "Możesz wskazać dwa wyniki w tym meczu."),
        ("CHANGE_MIND", "↺", "Możesz zmienić typ do 60 minut po rozpoczęciu meczu."),
        ("SWAP", "⇄", "Zamień ten mecz na mecz, który przegrał z nim w Drafcie."),
        ("GOOOOOOOOAL", "G!", "Za każde 2 gole wybranej drużyny otrzymasz +1 bonusu."),
    ]
    score = UserRoundScore.objects.filter(user=request.user, round=active_round).first() if request.user.is_authenticated else None
    score_by_match = {item["match"]: item for item in score.breakdown} if score else {}
    for match in matches:
        # The breakdown is keyed by the effective (possibly swapped) match.
        # Keep original_match only for the explanatory SWAP label in the UI.
        info = score_by_match.get(match.effective_match.id, {})
        match.match_points = info.get("typy", 0) + info.get("gole", 0) + info.get("bonus", 0)
        match.score_breakdown = info
        if match.effective_match.status == "CANCELLED":
            match.standard_score_state = match.goal_score_state = "neutral"
            match.score_state = "cancelled"
        elif match.effective_match.status == "FINISHED":
            match.standard_score_state = "hit" if info.get("standard_correct") is True else "miss" if info.get("standard_correct") is False else "neutral"
            match.goal_score_state = "hit" if info.get("goal_correct") is True else "miss" if info.get("goal_correct") is False else "neutral"
            match.score_state = match.standard_score_state
        else:
            match.standard_score_state = match.goal_score_state = match.score_state = "neutral"
    final_statuses = {"FINISHED", "CANCELLED"}
    # A user's SWAP replacement is part of that user's effective round too.
    # Do not offer an editing action after every effective match is settled.
    round_is_closed = bool(matches) and all(match.effective_match.status in final_statuses for match in matches)
    return render(request, "matches/obstaw.html", {"matches": matches, "active_round": active_round, "needs_confirmation": needs_confirmation, "chip_options": chip_options, "chip_error": chip_error, "active_modifier": active_round.active_global_modifier, "round_score": score, "chip_count": sum(bool(match.saved_chip) for match in matches), "round_is_closed": round_is_closed})
