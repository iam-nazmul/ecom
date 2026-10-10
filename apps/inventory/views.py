from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Product
from config.jinja2 import money

from .forms import SupplierPaymentForm
from .models import Purchase, StockMovement, Supplier
from .payments import PaymentError, apply_credit, record_payment, supplier_account, supplier_accounts, supplier_statement
from .services import InventoryError, inventory_summary, profit_and_loss, receive_purchase


def superuser_required(view):
    """Staff login required (redirect), then superuser required (403)."""
    @staff_member_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superusers can view this report.")
        return view(request, *args, **kwargs)
    return wrapper


@staff_member_required
def dashboard(request):
    products = Product.objects.select_related("category").order_by("name")
    return render(request, "inventory/dashboard.html", {
        "summary": inventory_summary(),
        "products": Paginator(products, 25).get_page(request.GET.get("page")),
        "movements": StockMovement.objects.select_related("product", "created_by")[:15],
    })


@staff_member_required
@require_POST
def receive(request, pk):
    purchase = get_object_or_404(Purchase, pk=pk)
    try:
        receive_purchase(purchase, user=request.user)
        messages.success(request, f"{purchase.reference} received into stock.")
    except InventoryError as exc:
        messages.error(request, str(exc))
    return redirect("inventory:dashboard")


@staff_member_required
def product_ledger(request, pk):
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)
    movements = product.stock_movements.select_related("created_by", "purchase", "order")
    return render(request, "inventory/ledger.html", {
        "product": product,
        "movements": Paginator(movements, 50).get_page(request.GET.get("page")),
    })


def _presets(today):
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    return {
        "this_month": ("This month", month_start, today),
        "last_month": ("Last month", last_month_end.replace(day=1), last_month_end),
        "last_30": ("Last 30 days", today - timedelta(days=29), today),
        "last_90": ("Last 90 days", today - timedelta(days=89), today),
        "this_year": ("This year", today.replace(month=1, day=1), today),
    }


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@superuser_required
def profit_loss(request):
    today = timezone.localdate()
    presets = _presets(today)
    preset = request.GET.get("range", "this_year")
    start, end = _parse_date(request.GET.get("start")), _parse_date(request.GET.get("end"))
    if start and end:
        preset = "custom"
        if start > end:
            start, end = end, start
    else:
        preset = preset if preset in presets else "this_year"
        _, start, end = presets[preset]
    report = profit_and_loss(start, end)
    peak = max([abs(m.net_profit) for m in report.months] + [m.total_income for m in report.months] + [1])
    return render(request, "inventory/profit_loss.html", {
        "report": report, "presets": presets, "preset": preset, "peak": peak,
    })


# ---------------------------------------------------------------- suppliers & payments


@staff_member_required
def supplier_list(request):
    accounts = sorted(supplier_accounts(), key=lambda a: (-a.overdue, -a.owed, a.supplier.name))
    totals = {
        "owed": sum((a.owed for a in accounts), Decimal("0")),
        "overdue": sum((a.overdue for a in accounts), Decimal("0")),
        "credit": sum((a.credit for a in accounts), Decimal("0")),
        "open_orders": sum((a.open_orders for a in accounts), Decimal("0")),
    }
    return render(request, "inventory/suppliers.html", {"accounts": accounts, "totals": totals})


@staff_member_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    form = SupplierPaymentForm(request.POST or None, supplier=supplier)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            payment = record_payment(
                supplier, data["amount"], date=data["date"], method=data["method"],
                reference=data["reference"], note=data["note"], user=request.user,
                allocate_to=form.allocation_target(),
            )
        except PaymentError as exc:
            messages.error(request, str(exc))
        else:
            left = payment.unallocated
            messages.success(
                request,
                f"Payment of {money(payment.amount)} recorded"
                + (f"; {money(left)} kept as supplier credit." if left > 0 else " and applied to purchases."),
            )
            return redirect(f"{reverse('inventory:supplier', args=[supplier.pk])}#payments")

    purchases = list(supplier.purchases.prefetch_related("items", "allocations__payment"))
    payments = list(supplier.payments.prefetch_related("allocations__purchase"))
    return render(request, "inventory/supplier_detail.html", {
        "supplier": supplier,
        "account": supplier_account(supplier, purchases, payments),
        "purchases": purchases,
        "payments": payments,
        "statement": supplier_statement(supplier),
        "form": form,
    })


@staff_member_required
@require_POST
def supplier_apply_credit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    applied = apply_credit(supplier)
    if applied:
        messages.success(request, f"Applied {money(applied)} of credit to open purchases.")
    else:
        messages.info(request, "No credit to apply, or nothing left to pay.")
    return redirect(f"{reverse('inventory:supplier', args=[supplier.pk])}#payments")
