from django.contrib.auth.views import LoginView
from django.urls import include, path

from orders import views

urlpatterns = [
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("api/orders/summary/", views.order_summary, name="order-summary"),
    path("demo/orders/summary/broken/", views.broken_order_summary,
         name="broken-order-summary"),
    path("silk/", include("silk.urls", namespace="silk")),
]
