from django.core.management.base import BaseCommand, CommandError

from email_queue.services import enqueue_email


class Command(BaseCommand):
    help = "Queue demo emails; the first can fail once to show exponential retry."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=100)
        parser.add_argument("--retry-first", action="store_true")
        parser.add_argument("--permanent-first", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["count"] <= 10000:
            raise CommandError("count must be between 1 and 10000")
        for number in range(options["count"]):
            job = enqueue_email(
                f"customer{number}@example.test", "Order confirmation", "Your order is confirmed.",
                demo_failures=1 if number == 0 and options["retry_first"] else 0,
                demo_permanent_failure=number == 0 and options["permanent_first"],
            )
            if number == 0:
                self.stdout.write(f"First job: {job.pk}")
        self.stdout.write(self.style.SUCCESS(f"Accepted {options['count']} email jobs"))
