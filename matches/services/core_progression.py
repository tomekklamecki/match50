"""CORE v2 rebuilds reuse tier unlocks and their durable event ledger."""
from django.db import transaction
from matches.models import AchievementOccurrence, UserAchievement, UserRoundScore
from matches.services.lifecycle import is_completed_round
from matches.services.streaks import streak_analysis
from matches.services.achievements import _tiers, TIERS, GOAL_TIERS, STREAK_TIERS, REPEAT_TIERS, CORE_CODES, CORE_NAMES


@transaction.atomic
def repeat_progress(user, code, name, events):
    events = list(dict.fromkeys(events))
    context = {"discovered": bool(events), "discovery_event": events[0] if events else None, "core_version": 2}
    _tiers(user, code, name, max(0, len(events)-1), REPEAT_TIERS, context)
    state = UserAchievement.objects.get(user=user, achievement__code=code)
    for index, event in enumerate(events):
        AchievementOccurrence.objects.get_or_create(user_achievement=state, event_key=event,
            defaults={"context": {"discovery": index == 0, "core_version": 2}})


@transaction.atomic
def rebuild_round_core(user):
    scores = [s for s in UserRoundScore.objects.filter(user=user).select_related("round").prefetch_related("round__matches").order_by("round__ranking_date", "round_id")
              if is_completed_round(s.round) and len(s.breakdown) == s.round.match_count]
    for code, name, field, threshold, tiers in (
        ("TYPY", "TYPY", "typy_points", 18, TIERS),
        ("GOLE", "GOLE", "gole_points", 5, GOAL_TIERS),
    ):
        _tiers(user, code, name, max((getattr(s, field) for s in scores), default=0), tiers, {"core_version": 2})
        repeat_progress(user, code+"_AGAIN", name+" — DO IT AGAIN",
                        [f"round:{s.round_id}" for s in scores if getattr(s, field) >= threshold])


@transaction.atomic
def rebuild_streak_repeat(user):
    repeat_progress(user, "STREAK_AGAIN", "SERIA — DO IT AGAIN", streak_analysis(user)[1])


@transaction.atomic
def rebuild_core(user):
    rebuild_round_core(user)
    streak, events = streak_analysis(user)
    _tiers(user, "STREAK", "SERIA", streak["max_streak"], STREAK_TIERS, streak)
    repeat_progress(user, "STREAK_AGAIN", "SERIA — DO IT AGAIN", events)


def core_achievement_points(user):
    return 2 * UserAchievement.objects.filter(user=user, unlocked=True,
        achievement__category__in=CORE_CODES, achievement__tier__in=CORE_NAMES).count()
