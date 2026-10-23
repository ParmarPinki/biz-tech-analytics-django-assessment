from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .selectors import get_orders_for_user, get_orders_without_prefetch


def build_summary(orders):
    """Shared response logic, unchanged between the broken and fixed examples."""
    order_count = 0
    item_count = 0
    total_amount = Decimal("0.00")

    for order in orders:
        order_count += 1
        # Without prefetch, this executes a separate SELECT for every order.
        for item in order.items.all():
            item_count += item.quantity
            total_amount += item.unit_price * item.quantity

    return {
        "order_count": order_count,
        "item_count": item_count,
        "total_amount": str(total_amount),
    }


@login_required
@require_GET
def order_summary(request):
    return JsonResponse(build_summary(get_orders_for_user(request.user)))


@login_required
@require_GET
def broken_order_summary(request):
    """Demo route only, retained to reproduce the original query behaviour."""
    return JsonResponse(build_summary(get_orders_without_prefetch(request.user)))
