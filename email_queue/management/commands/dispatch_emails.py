from django.core.management.base import BaseCommand

from email_queue.tasks import dispatch_pending


class Command(BaseCommand):
    help = "Republish due pending jobs directly, including after a broker outage."

    def handle(self, *args, **options):
        self.stdout.write(f"Published {dispatch_pending()} pending email jobs")
