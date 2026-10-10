from django.urls import path

from . import views

app_name = "pos"

urlpatterns = [
    path("", views.terminal, name="terminal"),
    path("open/", views.open_register, name="open"),
    path("sales/", views.sales, name="sales"),
    path("sales/<int:pk>/receipt/", views.receipt, name="receipt"),
    path("sales/<int:pk>/void/", views.void, name="void"),
    path("cash/", views.cash, name="cash"),
    path("close/", views.close, name="close"),
    path("sessions/<int:pk>/", views.session_report, name="session"),
    path("api/products/", views.api_products, name="api_products"),
    path("api/checkout/", views.api_checkout, name="api_checkout"),
]
