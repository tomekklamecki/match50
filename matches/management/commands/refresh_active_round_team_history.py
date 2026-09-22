from django.core.management.base import BaseCommand, CommandError
from matches.models import Round, Team
from matches.services.team_history import sync_round_team_history, sync_team_history


class Command(BaseCommand):
    help = 'Refresh 365 days of local team history for the active Round and its alternatives.'

    def add_arguments(self, parser):
        parser.add_argument('--team', type=int, help='Optional provider team ID for a targeted history sync.')

    def handle(self, *args, **options):
        if options['team'] is not None:
            teams = Team.objects.filter(api_football_id=options['team'])
            if not teams.exists():
                raise CommandError('No canonical Team with that provider ID.')
            report = sync_team_history(teams)
        else:
            round_ = Round.objects.filter(is_active=True).first()
            if round_ is None:
                self.stdout.write('No active Round; nothing to refresh.')
                return
            report = sync_round_team_history(round_)
        self.stdout.write(f'Fetched: {report.fetched}; created: {report.created}; updated: {report.updated}; unchanged: {report.unchanged}; errors: {len(report.errors)}')
        for error in report.errors:
            self.stderr.write(error)
        if report.errors:
            raise CommandError('History refresh partially failed; valid history retained. Retry is idempotent.')
