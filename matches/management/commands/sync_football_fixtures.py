from datetime import date
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from matches.services.api_football import ProviderError
from matches.services.football_sync import sync_fixtures


class Command(BaseCommand):
    help = "Import API-Football fixture facts without changing game results, scoring or Round membership."

    def add_arguments(self, parser):
        parser.add_argument("--competition", required=True, type=int)
        parser.add_argument("--season", required=True, type=int)
        parser.add_argument("--from", dest="date_from", type=date.fromisoformat)
        parser.add_argument("--to", dest="date_to", type=date.fromisoformat)

    def handle(self, *args, **options):
        if options["competition"] <= 0 or options["season"] <= 0:
            raise CommandError("Competition and season must be positive.")
        if bool(options["date_from"]) != bool(options["date_to"]) or (options["date_from"] and options["date_from"] > options["date_to"]):
            raise CommandError("Provide both --from and --to in chronological order.")
        try:
            report = sync_fixtures(options["competition"], options["season"], date_from=options["date_from"], date_to=options["date_to"])
        except (ProviderError, ValidationError, IntegrityError) as error:
            message = str(error) if isinstance(error, ProviderError) else "Canonical identity conflict; review admin bindings."
            raise CommandError(message) from None
        self.stdout.write(f"Fetched: {report.fetched}; created: {report.created}; updated: {report.updated}; unchanged: {report.unchanged}; errors: {len(report.errors)}")
        for error in report.errors:
            self.stderr.write(error)
        if report.errors:
            raise CommandError("Import completed with errors; valid fixtures were retained. Retry is idempotent.")
