from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from matches.services.achievements import evaluate_user


class Command(BaseCommand):
    help = "Idempotently backfill persisted achievement progress from existing scores; no reseed."

    def handle(self, *args, **options):
        for user in get_user_model().objects.iterator():
            evaluate_user(user)
        self.stdout.write(self.style.SUCCESS("Achievement state rebuilt."))
