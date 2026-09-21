from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from matches.services.core_progression import rebuild_core


class Command(BaseCommand):
    help = "Safely rebuild CORE v2; retain occurrence/notification history and all non-CORE achievements."

    @transaction.atomic
    def handle(self, *args, **options):
        count = 0
        for user in get_user_model().objects.all().iterator():
            rebuild_core(user)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Rebuilt CORE v2 for {count} players."))
