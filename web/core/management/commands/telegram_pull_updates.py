from django.core.management.base import BaseCommand

from core.telegram.handlers import pull_updates_and_process


class Command(BaseCommand):
    help = "Fetch Telegram updates with long polling and process them once."

    def add_arguments(self, parser):
        parser.add_argument("--offset", type=int, default=None, help="Start processing updates from this offset.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum number of updates to request.")
        parser.add_argument("--timeout", type=int, default=0, help="Telegram long-poll timeout in seconds.")

    def handle(self, *args, **options):
        result = pull_updates_and_process(
            offset=options["offset"],
            limit=options["limit"],
            timeout=options["timeout"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Telegram updates processed={result['processed']} next_offset={result['next_offset']}"
            )
        )
