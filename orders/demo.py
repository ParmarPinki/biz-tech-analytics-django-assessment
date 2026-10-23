from decimal import Decimal

from .models import Order, OrderItem


def create_demo_orders(user, count):
    """Three item rows per order: six units and a total of 18.75."""
    for _ in range(count):
        order = Order.objects.create(user=user)
        OrderItem.objects.bulk_create([
            OrderItem(order=order, quantity=1, unit_price=Decimal("10.00")),
            OrderItem(order=order, quantity=2, unit_price=Decimal("2.50")),
            OrderItem(order=order, quantity=3, unit_price=Decimal("1.25")),
        ])
