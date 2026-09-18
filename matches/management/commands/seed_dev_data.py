from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from matches.models import Competition, CompetitionSeason, Draft, GlobalModifier, Match, Match50Season, Round, Team, UserRoundScore


class Command(BaseCommand):
    help = "Creates idempotent local football development data."

    def handle(self, *args, **options):
        data = {
            "EPL": ("Premier League", "England", ["Arsenal", "Chelsea", "Liverpool", "Everton", "Manchester City", "Manchester United", "Tottenham", "West Ham United", "Newcastle United", "Aston Villa", "Brighton & Hove Albion", "Bournemouth", "Brentford", "Fulham", "Crystal Palace", "Wolverhampton Wanderers", "Burnley", "Leeds United", "Nottingham Forest", "Sunderland"], "Liverpool"),
            "LALIGA": ("La Liga", "Spain", ["Barcelona", "Real Madrid", "Atletico Madrid", "Sevilla"], "Barcelona"),
            "SERIEA": ("Serie A", "Italy", ["Inter", "Milan", "Juventus", "Napoli"], "Inter"),
            "BUNDESLIGA": ("Bundesliga", "Germany", ["Bayern Munich", "Borussia Dortmund", "Bayer Leverkusen", "RB Leipzig"], "Bayern Munich"),
        }
        seasons = {}
        for code, (name, country, teams, champion) in data.items():
            competition, _ = Competition.objects.get_or_create(code=code, defaults={"name": name, "country": country})
            team_map = {team: Team.objects.get_or_create(name=team)[0] for team in teams}
            seasons[code], _ = CompetitionSeason.objects.get_or_create(competition=competition, season_label="2026/27", defaults={"champion_team": team_map[champion]})
            if not seasons[code].champion_team_id:
                seasons[code].champion_team = team_map[champion]; seasons[code].save(update_fields=["champion_team"])
        modifier_data = [("HER_MAJESTY_EPL","HER MAJESTY: PREMIER LEAGUE","Każdy poprawny typ w meczu Premier League daje +1 BONUS."),("GOAL_FEST","GOAL FEST","Każdy poprawny typ w meczu, w którym padną co najmniej 4 gole, daje +1 BONUS."),("CLEAN_SHEET","CLEAN SHEET","Poprawny typ daje +1 BONUS, jeśli zwycięska drużyna zachowa czyste konto."),("ALL_HAIL_KING","ALL HAIL THE KING","Każdy poprawny typ w meczu z udziałem aktualnego mistrza kraju daje +1 BONUS.")]
        modifiers = {code: GlobalModifier.objects.get_or_create(code=code, defaults={"name": name, "description": description})[0] for code,name,description in modifier_data}
        round_ = Round.objects.filter(is_active=True).first()
        if round_:
            today = timezone.localdate()
            game_season, _ = Match50Season.objects.get_or_create(name=f"MATCH50 {today.year}", defaults={"starts_at": today.replace(month=1, day=1), "ends_at": today.replace(month=12, day=31), "is_active": True})
            if not game_season.is_active and not Match50Season.objects.filter(is_active=True).exists():
                game_season.is_active = True; game_season.save(update_fields=["is_active"])
            Round.objects.filter(match50_season__isnull=True).update(match50_season=game_season, ranking_date=today)
            history, _ = Round.objects.get_or_create(
                name="Demo ranking history",
                defaults={"ranking_date": today - timedelta(days=7), "match50_season": game_season, "match_count": 30},
            )
            for username, current_values, history_values in [
                ("demo_rank_alfa", (9, 3, 2), (6, 2, 1)),
                ("demo_rank_beta", (8, 4, 2), (6, 2, 1)),
                ("demo_rank_gamma", (8, 4, 2), (6, 2, 1)),
                ("demo_rank_delta", (7, 2, -2), (4, 1, -1)),
            ]:
                user, _ = get_user_model().objects.get_or_create(username=username)
                UserRoundScore.objects.get_or_create(user=user, round=round_, defaults={"typy_points": current_values[0], "gole_points": current_values[1], "bonus_points": current_values[2]})
                UserRoundScore.objects.get_or_create(user=user, round=history, defaults={"typy_points": history_values[0], "gole_points": history_values[1], "bonus_points": history_values[2]})
            round_.active_global_modifier = modifiers["HER_MAJESTY_EPL"]; round_.save(update_fields=["active_global_modifier"])
            fixtures = [("EPL","Arsenal","Chelsea"),("EPL","Liverpool","Tottenham"),("LALIGA","Barcelona","Sevilla"),("LALIGA","Real Madrid","Atletico Madrid"),("SERIEA","Inter","Milan"),("SERIEA","Juventus","Napoli"),("BUNDESLIGA","Bayern Munich","RB Leipzig"),("BUNDESLIGA","Borussia Dortmund","Bayer Leverkusen")]
            for code,home,away in fixtures:
                Match.objects.get_or_create(round=round_, home_team=home, away_team=away, defaults={"league": seasons[code].competition.name, "kickoff": timezone.now()+timedelta(days=7), "competition_season": seasons[code], "home_team_entity": Team.objects.get(name=home), "away_team_entity": Team.objects.get(name=away)})
            # Explicitly repair the original Stage 1 development fixtures.
            # This is data preparation, not runtime classification by text.
            legacy_epl_fixtures = [
                ("Arsenal", "Chelsea"), ("Liverpool", "Everton"),
                ("Manchester City", "Manchester United"), ("Tottenham", "West Ham United"),
                ("Newcastle United", "Aston Villa"), ("Brighton & Hove Albion", "Bournemouth"),
                ("Brentford", "Fulham"), ("Crystal Palace", "Wolverhampton Wanderers"),
                ("Burnley", "Leeds United"), ("Nottingham Forest", "Sunderland"),
            ]
            for home, away in legacy_epl_fixtures:
                Match.objects.filter(round=round_, home_team=home, away_team=away).update(
                    competition_season=seasons["EPL"], home_team_entity=Team.objects.get(name=home), away_team_entity=Team.objects.get(name=away)
                )
        draft = Draft.objects.filter(is_active=True).first()
        if draft:
            draft.modifier_options.set([modifiers["GOAL_FEST"], modifiers["CLEAN_SHEET"], modifiers["ALL_HAIL_KING"]])
        self.stdout.write(self.style.SUCCESS("Development football data is ready."))
