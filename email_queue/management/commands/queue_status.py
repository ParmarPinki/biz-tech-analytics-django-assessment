from django.core.management.base import BaseCommand
from django.db.models import Count

from email_queue.models import EmailJob


class Command(BaseCommand):
    help = "Show pending, sent and dead-letter counts."

    def handle(self, *args, **options):
        counts = {row["status"]: row["count"] for row in
                  EmailJob.objects.values("status").annotate(count=Count("id"))}
        for status in EmailJob.Status.values:
            self.stdout.write(f"{status}: {counts.get(status, 0)}")
        for job in EmailJob.objects.filter(status=EmailJob.Status.DEAD)[:10]:
            self.stdout.write(f"dead letter {job.pk}: {job.last_error}")
