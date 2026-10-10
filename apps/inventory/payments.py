"""Supplier payments (accounts payable).

A SupplierPayment is applied to purchases through PaymentAllocation rows; whatever is not
allocated is the supplier's credit (an advance). `Purchase.amount_paid` is a cached sum of
its allocations — only this module writes it.

Supplier balance = Σ total of *received* purchases − Σ payments
  > 0  we owe the supplier        < 0  the supplier owes us (credit / advance)
"""
from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import PaymentAllocation, Purchase, Supplier, SupplierPayment
from .services import InventoryError

ZERO = Decimal("0")


class PaymentError(InventoryError):
    pass


def _refresh_paid(purchases):
    """Re-derive the cached amount_paid from allocations (the source of truth)."""
    for purchase in {p.pk: p for p in purchases}.values():
        paid = purchase.allocations.aggregate(total=Sum("amount"))["total"] or ZERO
        Purchase.objects.filter(pk=purchase.pk).update(amount_paid=paid, updated_at=timezone.now())
        purchase.amount_paid = paid


def _open_bills(supplier, exclude=(), lock=True, include_open_orders=False):
    """Unpaid purchases, oldest first. Received bills only, unless include_open_orders
    (paying an open PO in advance must be chosen explicitly)."""
    purchases = (
        (Purchase.objects.select_for_update() if lock else Purchase.objects)
        .filter(supplier=supplier)
        .exclude(status=Purchase.Status.CANCELLED)
        .exclude(pk__in=[p.pk for p in exclude])
        .prefetch_related("items")
        .order_by("received_at", "order_date", "pk")
    )
    received = [p for p in purchases if p.status == Purchase.Status.RECEIVED]
    advance = [p for p in purchases if p.status != Purchase.Status.RECEIVED] if include_open_orders else []
    return [p for p in received + advance if p.balance_due > 0]


def _allocate(payment, purchases, available):
    touched = []
    for purchase in purchases:
        if available <= 0:
            break
        take = min(available, purchase.balance_due)
        if take <= 0:
            continue
        allocation, created = PaymentAllocation.objects.get_or_create(
            payment=payment, purchase=purchase, defaults={"amount": take}
        )
        if not created:
            allocation.amount += take
            allocation.save(update_fields=["amount"])
        purchase.amount_paid += take  # keep balance_due current for the next loop step
        available -= take
        touched.append(purchase)
    return touched, available


AUTO = "auto"
CREDIT = "credit"


def record_payment(supplier, amount, *, date=None, method=SupplierPayment.Method.BANK, reference="", note="",
                   user=None, allocate_to=AUTO):
    """Record a payment and allocate it.

    allocate_to: AUTO (oldest unpaid *received* purchases first; excess becomes credit),
    CREDIT (keep it all as credit), or a Purchase (pay that one first — an open PO counts
    as an advance; any excess then goes oldest-first).
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise PaymentError("Payment amount must be greater than zero.")
    with transaction.atomic():
        payment = SupplierPayment.objects.create(
            supplier=supplier, amount=amount, date=date or timezone.localdate(), method=method,
            reference=reference, note=note, created_by=user if user and user.is_authenticated else None,
        )
        available = amount
        touched = []
        if isinstance(allocate_to, Purchase):
            target = Purchase.objects.select_for_update().prefetch_related("items").get(pk=allocate_to.pk)
            if target.supplier_id != supplier.pk:
                raise PaymentError(f"{target.reference} belongs to another supplier.")
            if target.status == Purchase.Status.CANCELLED:
                raise PaymentError(f"{target.reference} is cancelled.")
            touched, available = _allocate(payment, [target], available)
            allocate_to = AUTO if available > 0 else CREDIT
            exclude = [target]
        else:
            exclude = []
        if allocate_to == AUTO and available > 0:
            more, available = _allocate(payment, _open_bills(supplier, exclude), available)
            touched += more
        _refresh_paid(touched)
    return payment


def payable_purchases(supplier):
    """Purchases a payment can still go to (read-only; for forms and pages)."""
    return _open_bills(supplier, lock=False, include_open_orders=True)


def apply_credit(supplier):
    """Use unallocated payment amounts (oldest first) to pay open bills (oldest first)."""
    applied = ZERO
    with transaction.atomic():
        payments = SupplierPayment.objects.select_for_update().filter(supplier=supplier).prefetch_related("allocations").order_by("date", "pk")
        bills = _open_bills(supplier)
        touched = set()
        for payment in payments:
            available = payment.unallocated
            if available <= 0:
                continue
            more, left = _allocate(payment, bills, available)
            applied += available - left
            touched.update(more)
            bills = [b for b in bills if b.balance_due > 0]
            if not bills:
                break
        _refresh_paid(touched)
    return applied


def release_allocations(purchase):
    """Un-apply payments from a purchase (e.g. when it is cancelled); the money becomes credit."""
    with transaction.atomic():
        released = purchase.allocations.aggregate(total=Sum("amount"))["total"] or ZERO
        purchase.allocations.all().delete()
        _refresh_paid([purchase])
    return released


def delete_payment(payment):
    with transaction.atomic():
        ids = list(payment.allocations.values_list("purchase_id", flat=True))
        purchases = list(Purchase.objects.select_for_update().filter(pk__in=ids))
        payment.delete()
        _refresh_paid(purchases)


# ---------------------------------------------------------------- reporting


@dataclass
class SupplierAccount:
    supplier: Supplier
    billed: Decimal         # received purchases
    paid: Decimal           # all payments
    open_orders: Decimal    # ordered/draft purchases not yet received (commitments)
    overdue: Decimal
    last_payment: date_cls | None

    @property
    def balance(self):
        return self.billed - self.paid

    @property
    def owed(self):
        return max(self.balance, ZERO)

    @property
    def credit(self):
        return max(-self.balance, ZERO)


def supplier_account(supplier, purchases=None, payments=None):
    purchases = list(supplier.purchases.prefetch_related("items")) if purchases is None else purchases
    payments = list(supplier.payments.all()) if payments is None else payments
    received = [p for p in purchases if p.status == Purchase.Status.RECEIVED]
    for p in purchases:
        p.supplier = supplier  # avoid a query per due_date
    return SupplierAccount(
        supplier=supplier,
        billed=sum((p.total for p in received), ZERO),
        paid=sum((p.amount for p in payments), ZERO),
        open_orders=sum((p.total for p in purchases if p.is_open), ZERO),
        overdue=sum((p.balance_due for p in received if p.is_overdue), ZERO),
        last_payment=max((p.date for p in payments), default=None),
    )


def supplier_accounts():
    suppliers = Supplier.objects.prefetch_related("purchases__items", "payments")
    return [supplier_account(s, list(s.purchases.all()), list(s.payments.all())) for s in suppliers]


def total_payable():
    return sum((a.owed for a in supplier_accounts()), ZERO)


@dataclass
class StatementLine:
    date: date_cls
    kind: str             # "bill" | "payment"
    description: str
    bill: Decimal
    payment: Decimal
    balance: Decimal
    obj: object


def supplier_statement(supplier):
    """Chronological bills (received purchases) and payments with a running balance owed."""
    events = []
    for p in supplier.purchases.filter(status=Purchase.Status.RECEIVED).prefetch_related("items"):
        events.append((timezone.localtime(p.received_at).date(), 0, p.pk, "bill", p))
    for pay in supplier.payments.prefetch_related("allocations__purchase"):
        events.append((pay.date, 1, pay.pk, "payment", pay))
    events.sort(key=lambda e: e[:3])
    balance = ZERO
    lines = []
    for day, _, _, kind, obj in events:
        if kind == "bill":
            balance += obj.total
            invoice = f" · invoice {obj.invoice_number}" if obj.invoice_number else ""
            lines.append(StatementLine(day, kind, f"{obj.reference} received{invoice}", obj.total, ZERO, balance, obj))
        else:
            balance -= obj.amount
            ref = f" · {obj.reference}" if obj.reference else ""
            lines.append(StatementLine(day, kind, f"Payment by {obj.get_method_display()}{ref}", ZERO, obj.amount, balance, obj))
    return lines
