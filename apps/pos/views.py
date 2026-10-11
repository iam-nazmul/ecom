import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.catalog.models import Category, Product

from .models import CashMovement, Payment, Register, RegisterSession, Sale
from .services import (
    PosError, add_cash_movement, can_void, checkout, close_session, current_session, max_adjustment_for, max_discount_for,
    open_session, session_summary, void_sale,
)

PRODUCT_LIMIT = 60


def _decimal(value, default=Decimal("0")):
    try:
        return Decimal(str(value).strip() or default)
    except (InvalidOperation, ValueError):
        raise PosError(f"“{value}” is not a valid amount.") from None


def _require_session(request):
    session = current_session(request.user)
    if session is None:
        messages.info(request, "Open a register to start selling.")
    return session


@staff_member_required
def terminal(request):
    session = current_session(request.user)
    if session is None:
        return render(request, "pos/open.html", {
            "registers": Register.objects.filter(is_active=True),
            "busy": set(RegisterSession.objects.filter(closed_at__isnull=True).values_list("register_id", flat=True)),
        })
    return render(request, "pos/terminal.html", {
        "session": session,
        "categories": Category.objects.filter(is_active=True),
        "methods": Payment.Method.choices,
        "max_discount": max_discount_for(request.user),
        "max_adjustment": max_adjustment_for(request.user),
        "currency": settings.CURRENCY_SYMBOL,
    })


@staff_member_required
@require_POST
def open_register(request):
    register = get_object_or_404(Register, pk=request.POST.get("register"))
    try:
        open_session(register, request.user, _decimal(request.POST.get("opening_cash", "0")))
        messages.success(request, f"{register} opened.")
    except PosError as exc:
        messages.error(request, str(exc))
    return redirect("pos:terminal")


@staff_member_required
@require_GET
def api_products(request):
    q = request.GET.get("q", "").strip()
    products = Product.objects.available().select_related("category").prefetch_related("images", "pos_barcodes")
    exact = None
    if q:
        exact = products.filter(Q(pos_barcodes__code=q) | Q(slug=q) | (Q(pk=q) if q.isdigit() else Q(pk__in=[]))).first()
        products = products.filter(Q(name__icontains=q) | Q(pos_barcodes__code__startswith=q)).distinct()
    if category := request.GET.get("category"):
        products = products.filter(category__slug=category)

    def row(p):
        return {
            "id": p.pk, "name": p.name, "price": str(p.price), "stock": p.stock,
            "category": p.category.name, "image": p.cover_url,
            "barcodes": [b.code for b in p.pos_barcodes.all()],
        }

    return JsonResponse({
        "exact": row(exact) if exact else None,
        "results": [row(p) for p in products.order_by("name")[:PRODUCT_LIMIT]],
    })


@staff_member_required
@require_POST
def api_checkout(request):
    session = current_session(request.user)
    if session is None:
        return JsonResponse({"ok": False, "error": "No open register. Reload the page."}, status=409)
    try:
        data = json.loads(request.body or "{}")
        sale = checkout(
            session, request.user,
            lines=[(line["id"], line["qty"]) for line in data.get("lines", [])],
            payments=[(p.get("method"), _decimal(p.get("amount", "0")), p.get("reference", "")) for p in data.get("payments", [])],
            discount_percent=_decimal(data.get("discount_percent", "0")),
            discount_amount=_decimal(data["discount_amount"]) if data.get("discount_amount") not in (None, "") else None,
            adjustment=_decimal(data.get("adjustment", "0")),
            customer_name=str(data.get("customer_name", ""))[:150],
            customer_phone=str(data.get("customer_phone", ""))[:30],
        )
    except PosError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)
    return JsonResponse({
        "ok": True, "sale": sale.number, "total": str(sale.total), "change": str(sale.change_given),
        "receipt_url": reverse("pos:receipt", args=[sale.pk]),
    })


@staff_member_required
def sales(request):
    session = current_session(request.user)
    session_id = request.GET.get("session") or (session.pk if session else None)
    viewing = get_object_or_404(RegisterSession.objects.select_related("register"), pk=session_id) if session_id else None
    rows = viewing.sales.select_related("cashier").prefetch_related("payments", "lines") if viewing else []
    return render(request, "pos/sales.html", {
        "session": session, "viewing": viewing, "sales": rows, "can_void": can_void(request.user),
    })


@staff_member_required
@require_POST
def void(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if not can_void(request.user):
        raise PermissionDenied("You don't have permission to void sales.")
    try:
        void_sale(sale, request.user, request.POST.get("reason", ""))
        messages.success(request, f"{sale.number} voided; its stock was returned.")
    except PosError as exc:
        messages.error(request, str(exc))
    return redirect(f"{reverse('pos:sales')}?session={sale.session_id}")


@staff_member_required
def cash(request):
    session = _require_session(request)
    if session is None:
        return redirect("pos:terminal")
    if request.method == "POST":
        try:
            add_cash_movement(session, request.POST.get("kind"), _decimal(request.POST.get("amount", "0")),
                              request.POST.get("reason", ""), request.user)
            messages.success(request, "Cash movement recorded.")
            return redirect("pos:cash")
        except PosError as exc:
            messages.error(request, str(exc))
    return render(request, "pos/cash.html", {
        "session": session, "summary": session_summary(session),
        "movements": session.cash_movements.select_related("created_by"), "kinds": CashMovement.Kind.choices,
    })


@staff_member_required
def close(request):
    session = _require_session(request)
    if session is None:
        return redirect("pos:terminal")
    if request.method == "POST":
        try:
            closed = close_session(session, request.user, _decimal(request.POST.get("counted_cash", "")), request.POST.get("note", ""))
            messages.success(request, f"{closed.register} closed.")
            return redirect("pos:session", pk=closed.pk)
        except PosError as exc:
            messages.error(request, str(exc))
    return render(request, "pos/close.html", {"session": session, "summary": session_summary(session)})


@staff_member_required
def session_report(request, pk):
    session = get_object_or_404(RegisterSession.objects.select_related("register", "opened_by", "closed_by"), pk=pk)
    if not (request.user.is_superuser or session.opened_by_id == request.user.pk):
        raise PermissionDenied("You can only view your own register sessions.")
    return render(request, "pos/session.html", {"session": session, "summary": session_summary(session)})


@staff_member_required
def receipt(request, pk):
    sale = get_object_or_404(Sale.objects.select_related("session__register", "cashier").prefetch_related("lines", "payments"), pk=pk)
    return render(request, "pos/receipt.html", {
        "sale": sale, "store": settings.POS_STORE_NAME, "address": settings.POS_STORE_ADDRESS,
        "footer": settings.POS_RECEIPT_FOOTER, "printed_at": timezone.now(),
    })
