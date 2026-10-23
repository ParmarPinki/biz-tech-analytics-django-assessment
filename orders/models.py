from django.conf import settings
from django.db import models


class Order(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    # Price at purchase time, so later product price changes do not change history.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=1),
                                   name="item_quantity_at_least_one"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0),
                                   name="item_price_not_negative"),
        ]
