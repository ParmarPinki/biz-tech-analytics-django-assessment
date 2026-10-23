import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from silk.collector import DataCollector

from .demo import create_demo_orders
from .models import Order, OrderItem
from .views import broken_order_summary, order_summary


class OrderSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="customer")
        cls.other_user = get_user_model().objects.create_user(username="other-customer")

    def request_summary(self, view):
        # Test only the view's SQL, excluding authentication and profiler overhead.
        # Silk can retain request state after a previous middleware-based test.
        DataCollector().clear()
        request = RequestFactory().get("/api/orders/summary/")
        request.user = self.user
        with CaptureQueriesContext(connection) as queries:
            response = view(request)
        return json.loads(response.content), len(queries)

    def test_fix_keeps_totals_and_removes_query_growth(self):
        create_demo_orders(self.user, 1)
        for count in (1, 250):
            if count == 250:
                create_demo_orders(self.user, 249)
            with self.subTest(orders=count):
                before, before_count = self.request_summary(broken_order_summary)
                after, after_count = self.request_summary(order_summary)
                self.assertEqual(before, after)
                self.assertEqual(after, {
                    "order_count": count,
                    "item_count": count * 6,
                    "total_amount": str(Decimal("18.75") * count),
                })
                self.assertEqual(before_count, count + 1)
                self.assertEqual(after_count, 2)

    def test_customer_without_orders(self):
        result, count = self.request_summary(order_summary)
        self.assertEqual(result, {"order_count": 0, "item_count": 0, "total_amount": "0.00"})
        self.assertEqual(count, 1)  # No item query when there are no orders.

    def test_orders_without_items_are_still_counted(self):
        Order.objects.create(user=self.user)
        result, count = self.request_summary(order_summary)
        self.assertEqual(result, {"order_count": 1, "item_count": 0, "total_amount": "0.00"})
        self.assertEqual(count, 2)

    def test_other_customers_orders_are_excluded(self):
        create_demo_orders(self.user, 1)
        other_order = Order.objects.create(user=self.other_user)
        OrderItem.objects.create(order=other_order, quantity=99, unit_price=Decimal("999.99"))
        result, _ = self.request_summary(order_summary)
        self.assertEqual(result, {"order_count": 1, "item_count": 6, "total_amount": "18.75"})

    def test_login_is_required_on_both_routes(self):
        for path in ("/api/orders/summary/", "/demo/orders/summary/broken/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertRedirects(response, f"/accounts/login/?next={path}",
                                     fetch_redirect_response=False)
