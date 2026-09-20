from django.utils import timezone
from matches.models import Achievement, AchievementNotification, ChipAssignment, Match, MatchKickoffSnapshot, TrophyFinish, UserAchievement, UserRoundScore
from matches.services.rankings import month_ranking, round_ranking, season_ranking
from matches.services.streaks import typy_streaks

TIERS=[("BRONZE",10),("SILVER",15),("GOLD",20),("PLATINUM",25),("DIAMOND",30)]
GOAL_TIERS=[("BRONZE",2),("SILVER",4),("GOLD",6),("PLATINUM",8),("DIAMOND",10)]

def _unlock(user, code, name, category, tier="", context=None, hidden=False):
    achievement,_=Achievement.objects.get_or_create(code=code,defaults={"name":name,"description":"","category":category,"tier":tier,"hidden":hidden})
    unlock, created = UserAchievement.objects.get_or_create(user_id=user if isinstance(user,int) else user.id,achievement=achievement,defaults={"context_data":context or {}})
    if created: AchievementNotification.objects.get_or_create(user_achievement=unlock)
    return unlock, created

def _tiers(user,prefix,name,value,thresholds,context):
    newly=[]
    for tier,threshold in thresholds:
        if value>=threshold:
            unlock,created=_unlock(user,f"{prefix}_{tier}",f"{name} {tier}",prefix,tier,{**context,"progress":value})
            if created:newly.append(unlock)
    return newly[-1:] # only highest tier notification per path/event

def evaluate_user(user):
    notifications=[]
    # First-achievement provenance must be evaluated in gameplay chronology.
    # _unlock uses get_or_create, so the first legitimate context persists.
    scores=UserRoundScore.objects.filter(user=user).select_related("round").order_by("round__ranking_date", "round__id")
    for score in scores:
        if score.round.matches.exclude(status__in=["FINISHED","CANCELLED"]).exists(): continue
        notifications+=_tiers(user,"TYPY","TYPY",score.typy_points,TIERS,{"round":score.round_id})
        notifications+=_tiers(user,"GOLE","GOLE",score.gole_points,GOAL_TIERS,{"round":score.round_id})
    completed = [score for score in scores if not score.round.matches.exclude(status__in=["FINISHED","CANCELLED"]).exists()]
    notifications += _tiers(user, "TYPY_AGAIN", "TYPY DO IT AGAIN", sum(score.typy_points >= 20 for score in completed), [("BRONZE",2),("SILVER",4),("GOLD",10),("PLATINUM",20),("DIAMOND",50)], {})
    notifications += _tiers(user, "GOLE_AGAIN", "GOLE DO IT AGAIN", sum(score.gole_points >= 6 for score in completed), [("BRONZE",2),("SILVER",4),("GOLD",10),("PLATINUM",20),("DIAMOND",50)], {})
    streak=typy_streaks(user)
    notifications+=_tiers(user,"STREAK","STREAK",streak["max_streak"],[("BRONZE",3),("SILVER",8),("GOLD",15),("PLATINUM",22),("DIAMOND",30)],streak)
    if streak["max_streak"]>=10:
        unlock,created=_unlock(user,"PERFECT_TEN","Perfect Ten","HIDDEN",context=streak,hidden=True)
        if created: notifications.append(unlock)
    mastery_codes={"EPL":"Premier League","LALIGA":"La Liga","BUNDESLIGA":"Bundesliga","SERIEA":"Serie A","LIGUE1":"Ligue 1","EKSTRAKLASA":"Ekstraklasa","UCL":"UEFA Champions League","UEL":"UEFA Europa League","UECL":"UEFA Conference League"}
    assignments={(item.round_id,item.match_id):item for item in ChipAssignment.objects.filter(user=user).select_related("replacement_match")}
    for code,name in mastery_codes.items():
        correct=0
        for score in scores:
            for item in score.breakdown:
                if not item.get("typy"): continue
                match=Match.objects.filter(pk=item.get("effective_match")).select_related("competition_season__competition").first()
                if match and match.competition_season and match.competition_season.competition.code==code: correct+=1
        notifications += _tiers(user,f"MASTERY_{code}",name,correct,[("BRONZE",10),("SILVER",25),("GOLD",50),("PLATINUM",100),("DIAMOND",250)],{"competition":code})
    return notifications

def evaluate_trophies(round_):
    entries=round_ranking(round_)
    leaders=[entry for entry in entries if entry.rank==1]
    for entry in entries:
        if entry.rank<=3:
            TrophyFinish.objects.get_or_create(user_id=entry.user_id,scope="ROUND",period_key=str(round_.id),defaults={"rank":entry.rank})
            if entry.rank==1:_unlock(entry.user_id,"ROUND_CHAMPION","Round Champion","CHAMPION",context={"round":round_.id,"total":entry.total})
            if entry.rank==1 and len(leaders)>1:_unlock(entry.user_id,"PHOTO_FINISH","Photo Finish","HIDDEN",context={"round":round_.id,"leaders":len(leaders)},hidden=True)


def evaluate_period_trophies(scope, period, entries, key):
    champion = f"{scope}_CHAMPION"
    for entry in entries:
        if entry.rank <= 3:
            TrophyFinish.objects.get_or_create(user_id=entry.user_id, scope=scope, period_key=key, defaults={"rank":entry.rank})
        if entry.rank == 1:
            _unlock(entry.user_id, champion, f"{scope.title()} Champion", "CHAMPION", context={"period":key,"total":entry.total})


def evaluate_month_trophies(year, month):
    from matches.services.rankings import month_ranking
    evaluate_period_trophies("MONTH", f"{year:04d}-{month:02d}", month_ranking(year, month), f"{year:04d}-{month:02d}")


def evaluate_season_trophies(season):
    from matches.services.rankings import season_ranking
    evaluate_period_trophies("SEASON", season.id, season_ranking(season), str(season.id))


def evaluate_snapshot_achievements(user):
    for score in UserRoundScore.objects.filter(user=user):
        for item in score.breakdown:
            if not item.get("typy"): continue
            match=Match.objects.filter(pk=item.get("effective_match")).first()
            if not match: continue
            snapshot=MatchKickoffSnapshot.objects.filter(match=match).first()
            if not snapshot: continue
            choices=item.get("standard_prediction",[]); selected=choices[0] if choices else None
            total=sum(snapshot.counts.get(value,0) for value in ("1","X","2"))
            percentage=(snapshot.counts.get(selected,0)*100/total) if total else 100
            context={"match":match.id,"percentage":percentage,"submitted":total}
            if total>=101 and percentage<1: _unlock(user,"LONE_WOLF","Lone Wolf","HIDDEN",context=context,hidden=True)
            if percentage<20: _unlock(user,f"DAVID_{match.id}","David","HIDDEN",context=context,hidden=True)


def consume_notifications(user):
    pending=AchievementNotification.objects.filter(user_achievement__user=user,consumed_at__isnull=True).select_related("user_achievement__achievement")
    items=list(pending)
    pending.update(consumed_at=timezone.now())
    return items
