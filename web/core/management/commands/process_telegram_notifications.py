from django.core.management.base import BaseCommand
from django.utils import timezone

from core.telegram.services import process_notification_queue


class Command(BaseCommand):
    help = "Queue and deliver Telegram notifications and reminders."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Maximum number of pending notifications to deliver.")

    def handle(self, *args, **options):
        result = process_notification_queue(now=timezone.now(), limit=options["limit"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Telegram notifications: queued={result['queued']} delivered={result['delivered']}"
            )
        )
