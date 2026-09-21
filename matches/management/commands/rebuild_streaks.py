from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from matches.services.achievements import refresh_streak_progress


class Command(BaseCommand):
    help = "Rebuild persisted SERIA state from frozen round orders after migration."

    @transaction.atomic
    def handle(self, *args, **options):
        count = 0
        for user in get_user_model().objects.all().iterator():
            refresh_streak_progress(user)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Rebuilt streak state for {count} players."))
