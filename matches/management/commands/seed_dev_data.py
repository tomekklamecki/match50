from datetime import datetime, time, timedelta
import random

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from matches.models import ChipAssignment, Competition, CompetitionSeason, Draft, DraftPair, GlobalModifier, Match, Match50Season, Prediction, Round, Team, UserRoundScore
from matches.services.scoring import recalculate_user_round_score
from matches.services.achievements import evaluate_user, evaluate_trophies, evaluate_month_trophies


class Command(BaseCommand):
    help = "Creates idempotent local football development data."

    def _clear_canonical_history(self):
        """Delete only the disposable, seed-owned historical fixture graph.

        The namespace is deliberately narrow: it never reaches the active
        lifecycle, real users, or manually-created application data.  Pair
        membership is the ownership link for the unrounded loser fixtures.
        """
        old_rounds = list(Round.objects.filter(name__startswith="DEV PROFILE HISTORY"))
        old_drafts = list(Draft.objects.filter(name__startswith="DEV HISTORY ORIGIN "))
        winner_ids = set(Match.objects.filter(round__in=old_rounds).values_list("id", flat=True))
        paired_match_ids = set(
            match_id
            for pair in DraftPair.objects.filter(draft__in=old_drafts)
            for match_id in (pair.match_a_id, pair.match_b_id)
        )
        loser_ids = paired_match_ids - winner_ids

        # Delete dependants before their fixtures.  This also removes an
        # interrupted-run prediction on an already paired loser without any
        # special orphan-repair branch.
        ChipAssignment.objects.filter(round__in=old_rounds).delete()
        Prediction.objects.filter(match_id__in=loser_ids).delete()
        Draft.objects.filter(pk__in=[draft.id for draft in old_drafts]).delete()
        Match.objects.filter(pk__in=loser_ids).delete()
        Round.objects.filter(pk__in=[round_.id for round_ in old_rounds]).delete()

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
            round_.active_global_modifier = modifiers["HER_MAJESTY_EPL"]; round_.save(update_fields=["active_global_modifier"])
            fixtures = [("EPL","Arsenal","Chelsea"),("EPL","Liverpool","Tottenham"),("LALIGA","Barcelona","Sevilla"),("LALIGA","Real Madrid","Atletico Madrid"),("SERIEA","Inter","Milan"),("SERIEA","Juventus","Napoli"),("BUNDESLIGA","Bayern Munich","RB Leipzig"),("BUNDESLIGA","Borussia Dortmund","Bayer Leverkusen")]
            for code,home,away in fixtures:
                if not Match.objects.filter(round=round_, home_team=home, away_team=away).exists():
                    Match.objects.create(round=round_, home_team=home, away_team=away, league=seasons[code].competition.name, kickoff=timezone.now()+timedelta(days=7), competition_season=seasons[code], home_team_entity=Team.objects.get(name=home), away_team_entity=Team.objects.get(name=away))
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
        # Four users share the same six historical Round fixtures.  Only
        # predictions vary, which makes ranking/profile verification honest.
        dev_users = [get_user_model().objects.get_or_create(username=username)[0] for username in (
            "demo_rank_alfa", "demo_rank_beta", "demo_rank_gamma", "demo_rank_delta",
        )]
        # Clean obsolete score-only development fixtures.  Point rows must
        # always be derived from Match/Prediction gameplay data.
        UserRoundScore.objects.filter(
            user__in=dev_users,
            round__name__in=["Demo ranking history", "DEV CURRENT ROUND"],
        ).delete()
        Prediction.objects.filter(user__username="tomekklama", match__round__name__startswith="DEV PROFILE HISTORY").delete()
        game_season = Match50Season.objects.filter(is_active=True).first()
        rng = random.Random(50)
        seed_midnight = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
        # Only seed fixtures from the deterministic team lists above.  This
        # keeps development history structurally valid (for example, no
        # Scottish fixture can accidentally be labelled as Premier League).
        historical_seasons = [
            (seasons[code], teams)
            for code, (_, _, teams, _) in data.items()
            if code in seasons
        ]
        # Canonical history is disposable, seed-owned data.  A clean rebuild
        # is safer than attempting to repair interrupted historical runs.
        self._clear_canonical_history()
        for round_number in range(6):
            history_round, _ = Round.objects.get_or_create(
                name=f"DEV PROFILE HISTORY {round_number + 1}",
                defaults={"ranking_date": timezone.localdate() - timedelta(days=45 - round_number * 7), "match50_season": game_season, "match_count": 30},
            )
            history_round.ranking_date = timezone.localdate() - timedelta(days=45 - round_number * 7)
            history_round.match50_season = game_season
            history_round.save(update_fields=["ranking_date", "match50_season"])
            if history_round.match_count != 30:
                history_round.match_count = 30
                history_round.save(update_fields=["match_count"])
            existing_matches = list(history_round.matches.order_by("id"))
            for match_number in range(30):
                key = round_number * 30 + match_number
                season, team_names = historical_seasons[key % len(historical_seasons)]
                home = team_names[(key * 2) % len(team_names)]
                away = team_names[(key * 2 + 1) % len(team_names)]
                home_goals, away_goals = rng.randrange(5), rng.randrange(5)
                kickoff = seed_midnight - timedelta(days=45 - round_number * 7, hours=match_number)
                match = existing_matches[match_number] if match_number < len(existing_matches) else Match(round=history_round)
                before = (match.home_team, match.away_team, match.kickoff, match.home_goals, match.away_goals, match.competition_season_id)
                match.home_team = home
                match.away_team = away
                match.league = season.competition.name
                match.competition_season = season
                match.home_team_entity = Team.objects.get(name=home)
                match.away_team_entity = Team.objects.get(name=away)
                match.kickoff = kickoff
                match.home_goals = home_goals
                match.away_goals = away_goals
                match.finished_at = kickoff + timedelta(hours=2)
                if match.pk is None or before != (match.home_team, match.away_team, match.kickoff, match.home_goals, match.away_goals, match.competition_season_id):
                    match.save()
                actual = "1" if home_goals > away_goals else "2" if away_goals > home_goals else "X"
                for user_number, user in enumerate(dev_users):
                    user_rng = random.Random(5000 + round_number * 100 + match_number * 7 + user_number)
                    prediction = actual if user_rng.random() < (.68 - user_number * .08) else user_rng.choice([value for value in ("1", "X", "2") if value != actual])
                    # Exactly ten valid GOLE submissions per 30-match round;
                    # the remaining twenty are deliberately left blank.
                    goals = (home_goals + away_goals if user_rng.random() < .45 else user_rng.randrange(8)) if (match_number + user_number * 2 + round_number) % 3 == 0 else None
                    Prediction.objects.update_or_create(user=user, match=match, defaults={"predicted_result": prediction, "total_goals": goals})
            # These are dedicated, system-owned development rounds.  Removing
            # stale duplicate fixture records is what keeps reruns at exactly
            # complete, 30-slot historical rounds and their predictions.
            for stale_match in existing_matches[30:]:
                stale_match.delete()
            # Scoring is recalculated once after the complete deterministic
            # fixture set has been prepared (rather than 24 times mid-seed).

        # STEP 1: persisted historical Draft provenance.  Winners are the
        # existing 30 global Round slots; paired losers never join the Round.
        for history_round in Round.objects.filter(name__startswith="DEV PROFILE HISTORY").order_by("ranking_date"):
            origin, _ = Draft.objects.get_or_create(
                name=f"DEV HISTORY ORIGIN {history_round.id}",
                defaults={"starts_at": history_round.ranking_date and seed_midnight - timedelta(days=60), "is_active": False, "next_round_name": history_round.name},
            )
            if not origin.pairs.exists():
                for number, winner in enumerate(history_round.matches.order_by("kickoff", "id")):
                    season = winner.competition_season
                    teams = historical_seasons[number % len(historical_seasons)][1]
                    home, away = teams[(number * 3 + 1) % len(teams)], teams[(number * 3 + 2) % len(teams)]
                    loser = Match.objects.create(
                        league=season.competition.name, competition_season=season,
                        home_team=home, away_team=away,
                        home_team_entity=Team.objects.get(name=home), away_team_entity=Team.objects.get(name=away),
                        kickoff=winner.kickoff, home_goals=(number + 1) % 4, away_goals=(number + 2) % 4,
                        finished_at=winner.finished_at,
                    )
                    pair = DraftPair.objects.create(draft=origin, day_number=number // 5 + 1, match_a=winner, match_b=loser)
                    pair.winner, pair.resolution_method, pair.resolved_at = winner, DraftPair.Resolution.VOTE, winner.finished_at
                    pair.save(update_fields=["winner", "resolution_method", "resolved_at"])

        # STEP 2: construct chip gameplay while the development fixtures are
        # genuinely editable, then restore their historical settled state.
        chip_order = [
            ChipAssignment.Chip.BANKER, ChipAssignment.Chip.DOUBLE_PICK,
            ChipAssignment.Chip.CHANGE_MIND, ChipAssignment.Chip.SWAP,
            ChipAssignment.Chip.GOOOOOOOAL,
        ]
        for round_number, history_round in enumerate(Round.objects.filter(name__startswith="DEV PROFILE HISTORY").order_by("ranking_date")):
            winners = list(history_round.matches.order_by("kickoff", "id"))
            pairs = {pair.winner_id: pair for pair in DraftPair.objects.filter(draft__name=f"DEV HISTORY ORIGIN {history_round.id}").select_related("match_a", "match_b")}
            fixtures = winners + [pair.match_b if pair.match_a_id == winner.id else pair.match_a for winner, pair in ((winner, pairs[winner.id]) for winner in winners)]
            snapshots = {fixture.id: (fixture.kickoff, fixture.home_goals if fixture.home_goals is not None else fixture.id % 4, fixture.away_goals if fixture.away_goals is not None else (fixture.id + 1) % 4, fixture.finished_at or fixture.kickoff + timedelta(hours=2)) for fixture in fixtures}
            Match.objects.filter(pk__in=snapshots).update(kickoff=seed_midnight + timedelta(days=90), home_goals=None, away_goals=None, finished_at=None, status=Match.Status.UPCOMING, result=None)
            for user_number, user in enumerate(dev_users):
                if ChipAssignment.objects.filter(user=user, round=history_round).exists():
                    continue
                slots = [(round_number * 5 + user_number * 3 + offset) % 30 for offset in range(5)]
                for chip, slot_index in zip(chip_order, slots):
                    winner = winners[slot_index]
                    pair = pairs[winner.id]
                    loser = pair.match_b if pair.match_a_id == winner.id else pair.match_a
                    winner.refresh_from_db()
                    loser.refresh_from_db()
                    if chip == ChipAssignment.Chip.SWAP:
                        previous = Prediction.objects.get(user=user, match=winner)
                        Prediction.objects.update_or_create(user=user, match=loser, defaults={"predicted_result": previous.predicted_result, "total_goals": previous.total_goals})
                        previous.delete()
                    defaults = {"outcomes": [], "goal_team": "", "replacement_match": None}
                    if chip == ChipAssignment.Chip.DOUBLE_PICK:
                        defaults["outcomes"] = ["1", "X"]
                    elif chip == ChipAssignment.Chip.SWAP:
                        defaults["replacement_match"] = loser
                    elif chip == ChipAssignment.Chip.GOOOOOOOAL:
                        defaults["goal_team"] = winner.home_team
                    ChipAssignment.objects.update_or_create(user=user, round=history_round, chip=chip, defaults={"match": winner, **defaults})
            for fixture_id, (kickoff, home_goals, away_goals, finished_at) in snapshots.items():
                Match.objects.filter(pk=fixture_id).update(kickoff=kickoff, home_goals=home_goals, away_goals=away_goals, finished_at=finished_at, status=Match.Status.FINISHED, result=("1" if home_goals > away_goals else "2" if home_goals < away_goals else "X"))

        # A separate active Round demonstrates mixed settled and editable
        # gameplay.  It is never reused as Draft input.
        if Round.objects.filter(name="DEV CURRENT ROUND").exists() or not Round.objects.filter(is_active=True).exists():
            current_round, _ = Round.objects.get_or_create(
                name="DEV CURRENT ROUND",
                defaults={"is_active": True, "ranking_date": timezone.localdate(), "match50_season": game_season, "match_count": 30},
            )
            existing = list(current_round.matches.order_by("id"))
            for number in range(30):
                season, team_names = historical_seasons[number % len(historical_seasons)]
                home, away = team_names[(number * 2) % len(team_names)], team_names[(number * 2 + 1) % len(team_names)]
                match = existing[number] if number < len(existing) else Match(round=current_round)
                match.home_team, match.away_team, match.league = home, away, season.competition.name
                match.competition_season = season
                match.home_team_entity, match.away_team_entity = Team.objects.get(name=home), Team.objects.get(name=away)
                match.kickoff = seed_midnight + timedelta(hours=number - 10)
                if number < 10:
                    match.home_goals, match.away_goals, match.finished_at = number % 4, (number + 1) % 4, match.kickoff + timedelta(hours=2)
                else:
                    match.home_goals = match.away_goals = match.finished_at = None
                match.save()
            for stale_match in existing[30:]: stale_match.delete()

        current_round = Round.objects.get(name="DEV CURRENT ROUND")
        # The current Round is the completed output of its own older Draft.
        # Persisted pairs, not team-name heuristics, are the source for SWAP.
        if not Draft.objects.filter(name="DEV CURRENT ORIGIN DRAFT").exists():
            origin = Draft.objects.create(name="DEV CURRENT ORIGIN DRAFT", starts_at=timezone.now() - timedelta(days=8), is_active=False, next_round_name=current_round.name)
            Round.objects.filter(pk=current_round.pk).update(is_active=False)
            try:
                for number, winner in enumerate(current_round.matches.order_by("kickoff", "id")):
                    season, team_names = historical_seasons[number % len(historical_seasons)]
                    home, away = team_names[(number * 5 + 2) % len(team_names)], team_names[(number * 5 + 3) % len(team_names)]
                    loser = Match.objects.create(league=season.competition.name, home_team=home, away_team=away, home_team_entity=Team.objects.get(name=home), away_team_entity=Team.objects.get(name=away), competition_season=season, kickoff=winner.kickoff + timedelta(days=1))
                    pair = DraftPair.objects.create(draft=origin, day_number=number // 5 + 1, match_a=winner, match_b=loser)
                    pair.winner = winner
                    pair.resolution_method = DraftPair.Resolution.VOTE
                    pair.resolved_at = timezone.now()
                    pair.save(update_fields=["winner", "resolution_method", "resolved_at"])
            finally:
                Round.objects.filter(pk=current_round.pk).update(is_active=True)

        # Day 3 Draft: days 1 and 2 have ten persisted winners in its future
        # Round; the remaining fifty candidates stay Draft-only.
        if not Draft.objects.filter(is_active=True).exists():
            draft = Draft.objects.create(name="DEV FUTURE DRAFT", starts_at=timezone.now() - timedelta(days=2), next_round_name="DEV FUTURE ROUND")
            draft.modifier_options.set([modifiers["GOAL_FEST"], modifiers["CLEAN_SHEET"], modifiers["ALL_HAIL_KING"]])
            candidates = []
            for number in range(60):
                season, team_names = historical_seasons[number % len(historical_seasons)]
                home, away = team_names[(number * 3) % len(team_names)], team_names[(number * 3 + 1) % len(team_names)]
                candidates.append(Match.objects.create(
                    league=season.competition.name, home_team=home, away_team=away,
                    home_team_entity=Team.objects.get(name=home), away_team_entity=Team.objects.get(name=away),
                    competition_season=season, kickoff=seed_midnight + timedelta(days=14, hours=number),
                ))
            for number in range(30):
                DraftPair.objects.create(draft=draft, day_number=number // 5 + 1, match_a=candidates[number * 2], match_b=candidates[number * 2 + 1])
            draft.resolve_closed_pairs()
        for history_round in Round.objects.filter(name__startswith="DEV PROFILE HISTORY"):
            for user in dev_users:
                recalculate_user_round_score(user, history_round)
            evaluate_trophies(history_round)
        for user in dev_users:
            evaluate_user(user)
        # Historical months are finalized development periods; the active
        # MATCH50 season deliberately receives no premature season trophy.
        for year, month in {(round_.ranking_date.year, round_.ranking_date.month) for round_ in Round.objects.filter(name__startswith="DEV PROFILE HISTORY")}:
            evaluate_month_trophies(year, month)
        self.stdout.write(self.style.SUCCESS("Development football data is ready."))
