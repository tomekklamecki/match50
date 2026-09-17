from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Draft, DraftPair, DraftVote, Prediction, Round


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
    return render(request, "matches/draft.html", {
        "draft": active_draft,
        "pairs": pairs,
        "current_day": current_day,
        "day_closes_at": active_draft.starts_at + timedelta(days=current_day),
        "completed_votes": completed_votes,
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


def typy(request):
    active_round = get_object_or_404(Round, is_active=True)
    matches = list(active_round.matches.order_by("kickoff", "id"))
    existing = {}
    if request.user.is_authenticated:
        existing = {item.match_id: item for item in Prediction.objects.filter(user=request.user, match__round=active_round)}
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect("login")
        goal_match_ids = {item.match_id for item in existing.values() if item.total_goals is not None}
        changes = []
        deletions = []
        for match in matches:
            result = request.POST.get(f"result_{match.id}")
            total_goals = request.POST.get(f"goals_{match.id}", "")
            if not match.predictions_editable:
                if result or total_goals:
                    return HttpResponseBadRequest("Predictions for matches at or after kickoff cannot be edited.")
                continue
            if result and result not in {"1", "X", "2"}:
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
            if result:
                changes.append((match, result, total_goals))
                if total_goals is None:
                    goal_match_ids.discard(match.id)
                else:
                    goal_match_ids.add(match.id)
            elif total_goals is not None:
                return HttpResponseBadRequest("Select a standard prediction before adding a goal prediction.")
            elif match.id in existing:
                deletions.append(match.id)
                goal_match_ids.discard(match.id)
        goal_count = len(goal_match_ids)
        if goal_count > 10:
            return HttpResponseBadRequest("A maximum of 10 goal predictions is allowed per round.")
        if goal_count < 10 and request.POST.get("confirm_less_than_ten") != "1":
            messages.warning(request, f"Nie wybrałeś 10 meczów do określenia liczby goli. Obecnie masz {goal_count}/10. Jeśli zapiszesz teraz, nie zdobędziesz punktów za pozostałe predykcje.")
            return render_typy(request, active_round, matches, existing, request.POST, True)
        if any(not match.predictions_editable for match, _, _ in changes):
            return HttpResponseBadRequest("Predictions for matches at or after kickoff cannot be edited.")
        with transaction.atomic():
            Prediction.objects.filter(user=request.user, match_id__in=deletions).delete()
            for match, result, total_goals in changes:
                Prediction.objects.update_or_create(user=request.user, match=match, defaults={"predicted_result": result, "total_goals": total_goals})
        messages.success(request, "Typy zostały zapisane.")
        return redirect("typy")
    return render_typy(request, active_round, matches, existing)


def render_typy(request, active_round, matches, existing, form_data=None, needs_confirmation=False):
    for match in matches:
        prediction = existing.get(match.id)
        match.saved_prediction = prediction.predicted_result if prediction else ""
        match.saved_goals = "" if not prediction or prediction.total_goals is None else prediction.total_goals
        if form_data is not None:
            match.saved_prediction = form_data.get(f"result_{match.id}", match.saved_prediction)
            match.saved_goals = form_data.get(f"goals_{match.id}", match.saved_goals)
    return render(request, "matches/obstaw.html", {"matches": matches, "active_round": active_round, "needs_confirmation": needs_confirmation})
