import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, ProgrammingError, close_old_connections
from django.utils import timezone

from core.telegram.handlers import pull_updates_and_process
from core.telegram.services import process_notification_queue


class Command(BaseCommand):
    help = "Run the Telegram bot loop: poll updates and dispatch queued notifications."

    def add_arguments(self, parser):
        parser.add_argument("--poll-timeout", type=int, default=30, help="Telegram long-poll timeout in seconds.")
        parser.add_argument("--notify-limit", type=int, default=100, help="Maximum number of queued notifications to deliver per cycle.")
        parser.add_argument("--retry-delay", type=int, default=5, help="Sleep duration after an operational error.")

    def handle(self, *args, **options):
        token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN is not configured.")

        poll_timeout = max(options["poll_timeout"], 0)
        notify_limit = max(options["notify_limit"], 1)
        retry_delay = max(options["retry_delay"], 1)
        offset = None

        self.stdout.write(self.style.SUCCESS("Telegram bot loop started."))
        while True:
            try:
                close_old_connections()
                notification_result = process_notification_queue(now=timezone.now(), limit=notify_limit)
                update_result = pull_updates_and_process(
                    offset=offset,
                    limit=100,
                    timeout=poll_timeout,
                )
                if update_result.get("next_offset") is not None:
                    offset = update_result["next_offset"]

                if notification_result["queued"] or notification_result["delivered"] or update_result["processed"]:
                    self.stdout.write(
                        "telegram cycle"
                        f" queued={notification_result['queued']}"
                        f" delivered={notification_result['delivered']}"
                        f" updates={update_result['processed']}"
                        f" next_offset={offset}"
                    )
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Telegram bot loop stopped."))
                return
            except (OperationalError, ProgrammingError) as exc:
                self.stderr.write(f"telegram bot waiting for database/migrations: {exc}")
                time.sleep(retry_delay)
            except Exception as exc:
                self.stderr.write(f"telegram bot cycle failed: {exc}")
                time.sleep(retry_delay)
