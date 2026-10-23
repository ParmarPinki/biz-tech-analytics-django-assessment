"""Make real requests through Silk and export the SQL it actually recorded."""
import json
import platform
from importlib.metadata import version
from pathlib import Path

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client
from django.utils import timezone
from silk.models import Request as SilkRequest

from orders.demo import create_demo_orders
from orders.models import Order, OrderItem


class Command(BaseCommand):
    help = "Profile the broken and fixed Task 1 routes with 250 demo orders."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="evidence/section1-profile.json")

    def handle(self, *args, **options):
        # A dedicated demo account keeps other users' data untouched.
        with transaction.atomic():
            user, created = get_user_model().objects.get_or_create(
                username="assessment-profile-demo"
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
                create_demo_orders(user, 250)
            orders = Order.objects.filter(user=user)
            items = OrderItem.objects.filter(order__user=user)
            if orders.count() != 250 or items.count() != 750:
                raise CommandError("Demo data changed. Use a fresh database for this command.")

        client = Client()
        client.force_login(user)
        results = {}
        for label, path in (
            ("before", "/demo/orders/summary/broken/"),
            ("after", "/api/orders/summary/"),
        ):
            # Identify this exact request without depending on request ordering.
            previous_ids = set(SilkRequest.objects.filter(path=path).values_list("id", flat=True))
            response = client.get(path)
            if response.status_code != 200:
                raise CommandError(f"{path} returned HTTP {response.status_code}")
            recorded = SilkRequest.objects.filter(path=path).exclude(id__in=previous_ids).get()
            queries = list(recorded.queries.order_by("start_time").values("query", "time_taken"))
            # Share profiler evidence without exporting a usable session identifier.
            session_key = client.cookies[settings.SESSION_COOKIE_NAME].value
            for query in queries:
                query["query"] = query["query"].replace(session_key, "[REDACTED_SESSION_KEY]")
            business_queries = [q for q in queries if
                                '"orders_order"' in q["query"] or
                                '"orders_orderitem"' in q["query"]]
            results[label] = {
                "path": path,
                "silk_request_id": str(recorded.pk),
                "http_status": response.status_code,
                "response": response.json(),
                "request_query_count": len(queries),
                "order_query_count": len(business_queries),
                "request_time_ms": recorded.time_taken,
                "sql_time_ms": sum(q["time_taken"] for q in queries),
                "queries": queries,
            }

        expected = {"order_count": 250, "item_count": 1500, "total_amount": "4687.50"}
        if any(result["response"] != expected for result in results.values()):
            raise CommandError("The demo response does not match the expected totals.")
        if (results["before"]["order_query_count"], results["after"]["order_query_count"]) != (251, 2):
            raise CommandError("Unexpected query counts; inspect the Silk requests.")

        report = {
            "generated_at": timezone.now().isoformat(),
            "environment": {
                "python": platform.python_version(),
                "django": django.get_version(),
                "django_silk": version("django-silk"),
                "database": "SQLite (local reproduction)",
            },
            "dataset": {"orders": 250, "item_rows": 750, "units": 1500},
            "measurement": "Actual django-silk SQL records; includes session/auth queries; session key redacted.",
            **results,
        }
        output = Path(options["output"])
        if not output.is_absolute():
            output = settings.BASE_DIR / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        for label, result in results.items():
            self.stdout.write(
                f"{label}: {result['order_query_count']} order queries, "
                f"{result['request_query_count']} total request queries"
            )
        self.stdout.write(self.style.SUCCESS(f"Silk evidence saved to {output}"))
