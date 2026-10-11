"""Point-of-sale rules.

A POS sale is recorded as a *delivered* `orders.Order` (walk-in customer, no shipping), so
stock (via the inventory ledger), cost of goods sold and the profit & loss report all work
without any code outside this app knowing about the POS. Voiding a sale cancels that order;
the inventory app's existing signal then returns the stock.
"""
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.inventory.services import lock_products, record_sale
from apps.orders.models import Order, OrderItem

from .models import CashMovement, Payment, Register, RegisterSession, Sale, SaleLine

ZERO = Decimal("0")
CENT = Decimal("0.01")
CASH = Payment.Method.CASH


class PosError(Exception):
    pass


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- sessions


def current_session(user):
    """The open session this user is working in, if any."""
    return (
        RegisterSession.objects.filter(opened_by=user, closed_at__isnull=True)
        .select_related("register").order_by("-opened_at").first()
    )


def open_session(register, user, opening_cash=ZERO):
    if not register.is_active:
        raise PosError(f"{register} is not active.")
    if current_session(user):
        raise PosError("You already have an open register. Close it first.")
    opening_cash = money(opening_cash)
    if opening_cash < 0:
        raise PosError("Opening cash can't be negative.")
    try:
        with transaction.atomic():
            return RegisterSession.objects.create(register=register, opened_by=user, opening_cash=opening_cash)
    except IntegrityError:
        raise PosError(f"{register} is already open by someone else.") from None


def add_cash_movement(session, kind, amount, reason, user):
    if not session.is_open:
        raise PosError("This register session is closed.")
    amount = money(amount)
    if amount <= 0:
        raise PosError("Amount must be greater than zero.")
    if not reason.strip():
        raise PosError("Give a reason for the cash movement.")
    if kind == CashMovement.Kind.OUT and amount > session_summary(session)["expected_cash"]:
        raise PosError("Not enough cash in the drawer.")
    return CashMovement.objects.create(session=session, kind=kind, amount=amount, reason=reason.strip(), created_by=user)


def session_summary(session):
    """Z-report figures. Voided sales are excluded (their money was handed back)."""
    sales = session.sales.all()
    completed = sales.filter(status=Sale.Status.COMPLETED)
    totals = completed.aggregate(
        count=Count("id"), gross=Sum("subtotal"), discounts=Sum("discount_amount"), adjustments=Sum("adjustment"),
        net=Sum("total"),
    )
    voided = sales.filter(status=Sale.Status.VOIDED).aggregate(count=Count("id"), total=Sum("total"))
    by_method = {
        row["method"]: money(row["total"])
        for row in Payment.objects.filter(sale__in=completed).values("method").annotate(total=Sum("amount")).order_by()
    }
    movements = session.cash_movements.aggregate(
        cash_in=Sum("amount", filter=Q(kind=CashMovement.Kind.IN)),
        cash_out=Sum("amount", filter=Q(kind=CashMovement.Kind.OUT)),
    )
    cash_sales = by_method.get(CASH, ZERO)
    cash_in, cash_out = movements["cash_in"] or ZERO, movements["cash_out"] or ZERO
    return {
        "sales_count": totals["count"],
        "gross": money(totals["gross"] or ZERO),
        "discounts": money(totals["discounts"] or ZERO),
        "adjustments": money(totals["adjustments"] or ZERO),
        "net": money(totals["net"] or ZERO),
        "voided_count": voided["count"],
        "voided_total": money(voided["total"] or ZERO),
        "by_method": [(label, by_method.get(value, ZERO)) for value, label in Payment.Method.choices],
        "opening_cash": session.opening_cash,
        "cash_sales": cash_sales,
        "cash_in": money(cash_in),
        "cash_out": money(cash_out),
        "expected_cash": money(session.opening_cash + cash_sales + cash_in - cash_out),
    }


def close_session(session, user, counted_cash, note=""):
    if not session.is_open:
        raise PosError("This register session is already closed.")
    counted_cash = money(counted_cash)
    if counted_cash < 0:
        raise PosError("Counted cash can't be negative.")
    with transaction.atomic():
        session = RegisterSession.objects.select_for_update().get(pk=session.pk)
        session.expected_cash = session_summary(session)["expected_cash"]
        session.counted_cash = counted_cash
        session.closed_at = timezone.now()
        session.closed_by = user
        session.note = note
        session.save()
    return session


# ---------------------------------------------------------------- selling


def max_discount_for(user):
    return Decimal("100") if user.is_superuser else settings.POS_MAX_DISCOUNT_PERCENT


def max_adjustment_for(user):
    """None = no limit."""
    return None if user.is_superuser else settings.POS_MAX_ADJUSTMENT


def _spread(amounts, reduction):
    """Take `reduction` off `amounts` in proportion to their size, to the cent (all in cents)."""
    whole = sum(amounts)
    if not reduction or not whole:
        return list(amounts)
    shares = [reduction * a // whole for a in amounts]
    leftover = reduction - sum(shares)
    by_remainder = sorted(range(len(amounts)), key=lambda i: (reduction * amounts[i]) % whole, reverse=True)
    for i in by_remainder[:leftover]:
        shares[i] += 1
    return [a - s for a, s in zip(amounts, shares)]


def _unit_prices(line_total, qty):
    """Split a line total (cents) into [(qty, unit price)] rows that add up to it exactly."""
    unit, extra = divmod(line_total, qty)
    rows = [(qty - extra, unit), (extra, unit + 1)]
    return [(q, money(Decimal(u) / 100)) for q, u in rows if q]


def checkout(session, user, lines, payments, *, discount_percent=ZERO, discount_amount=None, adjustment=ZERO,
             customer_name="", customer_phone=""):
    """Sell `lines` [(product_id, qty)] paid by `payments` [(method, amount, reference)].

    The discount is given either as a percentage or as an amount (`discount_amount`, which
    wins when given). `adjustment` is written off the amount due (e.g. rounding off the last
    few taka); it is never handed back as change.

    Prices always come from the database. Cash may exceed what's due (change is given);
    card/mobile payments may not. Discount and adjustment are spread over the lines so the
    order items add up to the amount charged, which is what the P&L counts as revenue.
    Everything is written in one transaction or not at all.
    """
    if not session.is_open:
        raise PosError("This register session is closed.")
    discount_percent = money(discount_percent)
    if not ZERO <= discount_percent <= 100:
        raise PosError("Discount must be between 0 and 100%.")
    if discount_amount is not None:
        discount_amount = money(discount_amount)
        if discount_amount < 0:
            raise PosError("Discount can't be negative.")
    elif discount_percent > max_discount_for(user):
        raise PosError(f"Your maximum discount is {max_discount_for(user)}%.")
    adjustment = money(adjustment)
    if adjustment < 0:
        raise PosError("Adjustment can't be negative.")
    limit = max_adjustment_for(user)
    if limit is not None and adjustment > limit:
        raise PosError(f"Your maximum adjustment is {settings.CURRENCY_SYMBOL}{limit}.")

    quantities = Counter()
    for product_id, qty in lines:
        qty = int(qty)
        if qty <= 0:
            raise PosError("Quantities must be at least 1.")
        quantities[int(product_id)] += qty
    if not quantities:
        raise PosError("The cart is empty.")

    tenders = []
    for method, amount, *rest in payments:
        if method not in Payment.Method.values:
            raise PosError(f"Unknown payment method: {method}.")
        amount = money(amount)
        if amount < 0:
            raise PosError("Payment amounts can't be negative.")
        if amount:
            tenders.append((method, amount, (rest[0] if rest else "") or ""))

    with transaction.atomic():
        # Re-read under lock: the register may have been closed since the page loaded.
        session = RegisterSession.objects.select_for_update().select_related("register").get(pk=session.pk)
        if not session.is_open:
            raise PosError("This register session is closed.")
        products = lock_products(quantities)
        for pid, qty in quantities.items():
            product = products.get(pid)
            if product is None or not product.is_active or not product.category.is_active:
                raise PosError("A product in the cart is no longer for sale.")
            if product.stock < qty:
                raise PosError(f"Only {product.stock} of {product.name} in stock.")

        sold = [(products[pid], qty) for pid, qty in quantities.items()]
        subtotal = sum((p.price * qty for p, qty in sold), ZERO)
        if discount_amount is None:
            discount_amount = money(subtotal * discount_percent / 100)
        else:
            if discount_amount > subtotal:
                raise PosError("Discount can't be more than the subtotal.")
            if discount_amount > money(subtotal * max_discount_for(user) / 100):
                raise PosError(f"Your maximum discount is {max_discount_for(user)}%.")
            discount_percent = money(discount_amount * 100 / subtotal) if subtotal else ZERO
        if adjustment > subtotal - discount_amount:
            raise PosError("Adjustment can't be more than the amount due.")
        total = subtotal - discount_amount - adjustment

        non_cash = sum((a for m, a, _ in tenders if m != CASH), ZERO)
        cash_received = sum((a for m, a, _ in tenders if m == CASH), ZERO)
        if non_cash > total:
            raise PosError("Card / mobile payments are more than the total.")
        cash_due = total - non_cash
        if cash_received < cash_due:
            raise PosError(f"Payment is short by {settings.CURRENCY_SYMBOL}{cash_due - cash_received}.")
        if adjustment and cash_received > cash_due:
            raise PosError("A sale with an adjustment can't give change. Lower the adjustment or the cash received.")

        net = _spread([int(p.price * qty * 100) for p, qty in sold], int((discount_amount + adjustment) * 100))
        priced = [(p, qty, line_total) for (p, qty), line_total in zip(sold, net)]

        order = Order.objects.create(
            full_name=customer_name.strip() or "Walk-in customer", email="", phone=customer_phone.strip(),
            address_line1="In-store sale (POS)", city=session.register.location or "In store", postal_code="-",
            country="-", notes=f"POS sale · {session.register.name}", status=Order.Status.DELIVERED,
            subtotal=total, shipping_cost=ZERO, total=total,
        )
        # A line whose total doesn't divide evenly by its quantity becomes two items one cent apart.
        items = OrderItem.objects.bulk_create(
            OrderItem(order=order, product=p, product_name=p.name, unit_price=unit, quantity=q)
            for p, qty, line_total in priced for q, unit in _unit_prices(line_total, qty)
        )
        record_sale(order, items, products)  # cost snapshot + stock ledger

        sale = Sale.objects.create(
            session=session, order=order, cashier=user, customer_name=customer_name.strip(),
            customer_phone=customer_phone.strip(), subtotal=subtotal, discount_percent=discount_percent,
            discount_amount=discount_amount, adjustment=adjustment, total=total,
            change_given=cash_received - cash_due,
        )
        SaleLine.objects.bulk_create(
            SaleLine(sale=sale, product=p, product_name=p.name, list_price=p.price,
                     unit_price=money(Decimal(line_total) / 100 / qty), quantity=qty)
            for p, qty, line_total in priced
        )
        rows = [Payment(sale=sale, method=m, amount=a, received=a, reference=ref) for m, a, ref in tenders if m != CASH]
        if cash_received:
            rows.append(Payment(sale=sale, method=CASH, amount=cash_due, received=cash_received))
        Payment.objects.bulk_create(rows)
    return sale


def can_void(user):
    return user.has_perm("pos.void_sale")


def void_sale(sale, user, reason):
    if not can_void(user):
        raise PosError("You don't have permission to void sales.")
    if not reason.strip():
        raise PosError("Give a reason for voiding the sale.")
    with transaction.atomic():
        sale = Sale.objects.select_for_update(of=("self",)).select_related("session", "order").get(pk=sale.pk)
        if sale.is_voided:
            raise PosError(f"{sale.number} is already voided.")
        if not sale.session.is_open:
            raise PosError("Only sales from an open register session can be voided.")
        if sale.order and sale.order.status != Order.Status.CANCELLED:
            sale.order.status = Order.Status.CANCELLED
            sale.order.save()  # inventory app restocks cancelled orders
        sale.status = Sale.Status.VOIDED
        sale.voided_at = timezone.now()
        sale.voided_by = user
        sale.void_reason = reason.strip()
        sale.save()
    return sale


def default_registers():
    return Register.objects.filter(is_active=True)
