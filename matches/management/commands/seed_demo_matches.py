from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from matches.models import Match, Round


class Command(BaseCommand):
    help = "Adds an active round with 10 Premier League fixtures tomorrow at 20:00."

    def handle(self, *args, **options):
        kickoff = timezone.make_aware(
            timezone.datetime.combine(
                timezone.localdate() + timedelta(days=1), time(20, 0)
            )
        )
        round_ = Round.objects.create(
            name="Premier League — jutro", is_active=True, match_count=10
        )
        fixtures = [
            ("Arsenal", "Chelsea"),
            ("Liverpool", "Everton"),
            ("Manchester City", "Manchester United"),
            ("Tottenham", "West Ham United"),
            ("Newcastle United", "Aston Villa"),
            ("Brighton & Hove Albion", "Bournemouth"),
            ("Brentford", "Fulham"),
            ("Crystal Palace", "Wolverhampton Wanderers"),
            ("Burnley", "Leeds United"),
            ("Nottingham Forest", "Sunderland"),
        ]
        Match.objects.bulk_create(
            [
                Match(
                    round=round_,
                    league="Premier League",
                    home_team=home_team,
                    away_team=away_team,
                    kickoff=kickoff,
                )
                for home_team, away_team in fixtures
            ]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Dodano 10 meczów na {timezone.localtime(kickoff):%d.%m.%Y %H:%M}."
            )
        )
