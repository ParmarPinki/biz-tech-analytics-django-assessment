"""The reconstructed deployment regression is in this queryset helper."""
from .models import Order


def get_orders_without_prefetch(user):
    # Broken version: removing prefetch still returns correct data, but is slower.
    return Order.objects.filter(user=user).order_by("id")


def get_orders_for_user(user):
    # Reverse ForeignKey: fetch all items in a second query, not one per order.
    return get_orders_without_prefetch(user).prefetch_related("items")
