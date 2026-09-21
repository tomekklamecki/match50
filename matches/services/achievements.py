"""Persisted achievement evaluation. Explicit services, never HTTP-time evaluation."""
from django.db import transaction
from django.utils import timezone
from matches.models import (Achievement, AchievementOccurrence, AchievementNotification,
    Match, MatchKickoffSnapshot, TrophyFinish, UserAchievement, UserRoundScore)
from matches.services.rankings import month_ranking, round_ranking, season_ranking
from matches.services.lifecycle import is_completed_round
from matches.services.streaks import typy_streaks

CORE_NAMES = ("BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND", "GODLIKE")
TIERS = list(zip(CORE_NAMES, (10,14,18,22,26,30)))
GOAL_TIERS = list(zip(CORE_NAMES, (1,3,5,7,9,10)))
REPEAT_TIERS = list(zip(CORE_NAMES, (1,3,8,15,25,50)))
STREAK_TIERS = list(zip(CORE_NAMES, (4,8,12,16,20,25)))
CORE_CODES = ("TYPY", "GOLE", "STREAK", "TYPY_AGAIN", "GOLE_AGAIN", "STREAK_AGAIN")
MASTERY_TIERS = list(zip(("BRONZE","SILVER","GOLD","PLATINUM","DIAMOND"), (10,25,50,100,250)))
LEAGUES = {"EPL":"Premier League","LALIGA":"La Liga","BUNDESLIGA":"Bundesliga",
    "SERIEA":"Serie A","LIGUE1":"Ligue 1","EKSTRAKLASA":"Ekstraklasa",
    "UCL":"Champions League","UEL":"Europa League","UECL":"Conference League"}

@transaction.atomic
def _unlock(user, code, name, category, tier="", context=None, hidden=False,
            repeatable=False, event_key="unlock", description=""):
    definition, _ = Achievement.objects.get_or_create(code=code, defaults={
        "name":name, "description":description, "category":category,
        "tier":tier, "hidden":hidden, "repeatable":repeatable})
    state, _ = UserAchievement.objects.get_or_create(
        user_id=user if isinstance(user,int) else user.pk, achievement=definition,
        defaults={"unlocked":False})
    state = UserAchievement.objects.select_for_update().get(pk=state.pk)
    key = event_key if definition.repeatable else "unlock"
    occurrence, created = AchievementOccurrence.objects.get_or_create(
        user_achievement=state, event_key=key, defaults={"context":context or {}})
    if not created:
        return state, False
    first = not state.unlocked
    state.unlocked = True
    state.current_tier = tier
    if first:
        state.unlocked_at = occurrence.earned_at
        state.context_data = context or {}
    if definition.repeatable:
        state.progress = state.occurrences.count()
        state.stars = min(definition.max_stars, state.progress)
    state.save()
    if first or not definition.repeatable or state.progress <= definition.max_stars:
        AchievementNotification.objects.get_or_create(user_achievement=state, event_key=key)
    return state, True

@transaction.atomic
def _tiers(user, prefix, name, value, thresholds, context):
    definition, _ = Achievement.objects.get_or_create(code=prefix, defaults={
        "name":name, "description":"", "category":"PROGRESS", "metadata":{"thresholds":thresholds}})
    state, _ = UserAchievement.objects.get_or_create(user=user, achievement=definition,
                                                    defaults={"unlocked":False})
    state = UserAchievement.objects.select_for_update().get(pk=state.pk)
    core = prefix in CORE_CODES
    if core:
        definition.metadata = {**definition.metadata, "thresholds": thresholds, "core": True}
        definition.save(update_fields=["metadata"])
        state.current_tier = ""
        UserAchievement.objects.filter(user=user, achievement__category=prefix, achievement__tier__in=CORE_NAMES).update(unlocked=False)
    state.progress = value if core else max(state.progress, value)
    state.context_data = context
    newly = []
    for tier, threshold in thresholds:
        if state.progress >= threshold:
            unlock, created = _unlock(user, f"{prefix}_{tier}", f"{name} {tier}", prefix,
                                      tier, {**context, "progress":state.progress})
            if core and not unlock.unlocked:
                unlock.unlocked = True
                unlock.save(update_fields=["unlocked"])
            state.current_tier = tier
            if created:
                newly.append(unlock)
    if not state.unlocked and state.current_tier:
        state.unlocked_at = timezone.now()
    state.unlocked = bool(state.current_tier)
    state.save()
    return newly

def _round_progress(user, score):
    if not is_completed_round(score.round):
        return
    # A SWAP replacement must also be settled before this user's round award.
    if len(score.breakdown) != score.round.match_count:
        return
    from matches.services.core_progression import rebuild_round_core
    rebuild_round_core(user)
    context = {"round":score.round_id}
    slots = {item.get("original_match", item.get("match")): item for item in score.breakdown}
    if (score.round.match_count == 30 and len(slots) == 30
            and all(item.get("standard_prediction") for item in slots.values())
            and sum(bool(item.get("standard_correct")) for item in slots.values()) <= 5):
        _unlock(user,"TWITTER_EXPERT","EKSPERT Z TWITTERA","ROUND",
                context=context,repeatable=True,event_key=f"round:{score.round_id}",
                description="Pewność siebie? 30/30. Forma? Do ciasta.")

def refresh_streak_progress(user):
    """Rebuild the existing streak state without changing other achievements."""
    streak = typy_streaks(user)
    UserAchievement.objects.filter(user=user, achievement__code="STREAK").update(progress=streak["max_streak"])
    _tiers(user,"STREAK","SERIA",streak["max_streak"],STREAK_TIERS,streak)
    from matches.services.core_progression import rebuild_streak_repeat
    rebuild_streak_repeat(user)
    if streak["max_streak"] >= 10:
        _unlock(user,"PERFECT_TEN","Perfect Ten","HIDDEN",context=streak,hidden=True)
    return streak


def _match_progress(user, score):
    refresh_streak_progress(user)
    counts = dict.fromkeys(LEAGUES, 0)
    for saved in UserRoundScore.objects.filter(user=user):
        for item in saved.breakdown:
            if not item.get("typy"):
                continue
            match = Match.objects.filter(pk=item.get("effective_match",item.get("match"))).select_related("competition_season__competition").first()
            if match and match.competition_season:
                code = match.competition_season.competition.code
                if code in counts:
                    counts[code] += 1
    for code, name in LEAGUES.items():
        _tiers(user,f"MASTERY_{code}",name,counts[code],MASTERY_TIERS,{"competition":code})
    _snapshot_items(user, score.breakdown)

def _snapshot_items(user, items):
    for item in items:
        if not item.get("typy"):
            continue
        match = Match.objects.filter(pk=item.get("effective_match",item.get("match")), status="FINISHED").first()
        snapshot = MatchKickoffSnapshot.objects.filter(match=match).first() if match else None
        if not snapshot:
            continue
        # Use the correct outcome, including when it is the SECOND Double Pick.
        from matches.services.scoring import actual_outcome
        selected = actual_outcome(match)
        if selected not in item.get("standard_prediction", []):
            continue
        total = sum(snapshot.counts.get(value,0) for value in ("1","X","2"))
        count = snapshot.counts.get(selected,0)
        context = {"match":match.pk,"submitted":total,"percentage":100*count/total if total else 100}
        if total >= 101 and count * 100 < total:
            _unlock(user,"LONE_WOLF","Lone Wolf","HIDDEN",context=context,hidden=True)
        if total and count * 5 < total:
            _unlock(user,f"DAVID_{match.pk}","David","HIDDEN",context=context,hidden=True)

# Small event registry: new evaluators can subscribe without changing dispatch.
EVALUATORS = {"match_finalized": (_match_progress,), "round_finalized": (_round_progress,)}

@transaction.atomic
def evaluate_score(score):
    for evaluator in EVALUATORS["match_finalized"]:
        evaluator(score.user, score)
    if is_completed_round(score.round):
        for evaluator in EVALUATORS["round_finalized"]:
            evaluator(score.user, score)

def evaluate_user(user):
    """Explicit administrative/backfill entry point, never called by profiles."""
    for score in UserRoundScore.objects.filter(user=user).select_related("round","user").order_by("round__ranking_date","round_id"):
        evaluate_score(score)
    return []

@transaction.atomic
def synchronize_round_trophies(round_):
    """Reconcile persisted medals with the completed round's current ranking."""
    if not is_completed_round(round_):
        return []
    entries = round_ranking(round_)
    podium = [entry for entry in entries if entry.rank <= 3]
    trophies = TrophyFinish.objects.filter(scope="ROUND", period_key=str(round_.pk))
    trophies.exclude(user_id__in=[entry.user_id for entry in podium]).delete()
    for entry in podium:
        TrophyFinish.objects.update_or_create(
            user_id=entry.user_id, scope="ROUND", period_key=str(round_.pk),
            defaults={"rank": entry.rank},
        )
    return entries


@transaction.atomic
def evaluate_trophies(round_):
    entries = synchronize_round_trophies(round_)
    leaders = [entry for entry in entries if entry.rank == 1]
    for entry in entries:
        if entry.rank == 1:
            _unlock(entry.user_id,"ROUND_CHAMPION","Mistrz kolejki","CHAMPION",
                    context={"round":round_.pk,"total":entry.total})
            if len(leaders)>1:
                _unlock(entry.user_id,"PHOTO_FINISH","Photo Finish","HIDDEN",
                        context={"round":round_.pk,"leaders":len(leaders)},hidden=True)

@transaction.atomic
def evaluate_period_trophies(scope, period, entries, key):
    for entry in entries:
        if entry.rank <= 3:
            TrophyFinish.objects.get_or_create(user_id=entry.user_id,scope=scope,period_key=key,defaults={"rank":entry.rank})
        if entry.rank == 1:
            _unlock(entry.user_id,f"{scope}_CHAMPION",f"{scope.title()} Champion","CHAMPION",context={"period":key,"total":entry.total})

def evaluate_month_trophies(year, month):
    if (year, month) >= (timezone.localdate().year, timezone.localdate().month):
        return
    evaluate_period_trophies("MONTH",None,month_ranking(year,month),f"{year:04d}-{month:02d}")

def evaluate_season_trophies(season):
    if season.ends_at >= timezone.localdate():
        return
    evaluate_period_trophies("SEASON",season.pk,season_ranking(season),str(season.pk))

def evaluate_snapshot_achievements(user):
    for score in UserRoundScore.objects.filter(user=user):
        _snapshot_items(user,score.breakdown)

def consume_notifications(user):
    pending=AchievementNotification.objects.filter(user_achievement__user=user,consumed_at__isnull=True).select_related("user_achievement__achievement")
    items=list(pending)
    pending.update(consumed_at=timezone.now())
    return items
