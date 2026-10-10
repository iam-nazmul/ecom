from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("inventory/", views.dashboard, name="dashboard"),
    path("inventory/products/<int:pk>/", views.product_ledger, name="ledger"),
    path("inventory/purchases/<int:pk>/receive/", views.receive, name="receive"),
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("suppliers/<int:pk>/", views.supplier_detail, name="supplier"),
    path("suppliers/<int:pk>/apply-credit/", views.supplier_apply_credit, name="apply_credit"),
    path("reports/profit-loss/", views.profit_loss, name="profit_loss"),
]
