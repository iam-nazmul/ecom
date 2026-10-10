"""Inventory rules: the stock ledger, weighted-average costing, purchasing and profit & loss.

All stock changes go through `record_movement()` on a row-locked product so that
Product.stock, Product.average_cost and the StockMovement ledger never disagree.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem

from .models import Expense, Income, Purchase, StockAdjustment, StockMovement

ZERO = Decimal("0")
CENT = Decimal("0.01")
COST_PLACES = Decimal("0.0001")
MONEY_FIELD = DecimalField(max_digits=16, decimal_places=4)


class InventoryError(Exception):
    pass


def _cost(value):
    return Decimal(value).quantize(COST_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- ledger


def record_movement(product, kind, quantity, unit_cost=None, *, at=None, user=None, **refs):
    """Apply a stock change to a product locked with select_for_update() and log it.

    Incoming stock (quantity > 0) blends into the weighted-average cost:
        new_avg = (stock × avg + qty × unit_cost) / (stock + qty)
    Outgoing stock leaves the average unchanged and is valued at it.
    """
    if quantity == 0:
        raise InventoryError("Quantity must not be zero.")
    new_stock = product.stock + quantity
    if new_stock < 0:
        raise InventoryError(f"Only {product.stock} of {product.name} in stock.")

    if quantity > 0:
        unit_cost = _cost(product.average_cost if unit_cost is None else unit_cost)
        if new_stock:
            product.average_cost = _cost((product.stock * product.average_cost + quantity * unit_cost) / new_stock)
    else:
        unit_cost = _cost(product.average_cost)

    product.stock = new_stock
    product.save(update_fields=["stock", "average_cost", "updated_at"])
    return StockMovement.objects.create(
        product=product, kind=kind, quantity=quantity, unit_cost=unit_cost, balance_after=new_stock,
        created_by=user if user and user.is_authenticated else None,
        created_at=at or timezone.now(), **refs,
    )


def record_opening_balance(product, unit_cost=ZERO, note="Opening balance"):
    """Log existing stock as the first ledger row, without changing the stock level."""
    product.average_cost = _cost(unit_cost)
    product.save(update_fields=["average_cost", "updated_at"])
    return StockMovement.objects.create(
        product=product, kind=StockMovement.Kind.OPENING, quantity=product.stock,
        unit_cost=product.average_cost, balance_after=product.stock, note=note,
    )


def lock_products(ids):
    return {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=set(ids)).order_by("pk")}


# ---------------------------------------------------------------- sales


def record_sale(order, items, products):
    """Snapshot cost on each OrderItem and remove the stock. Products must already be locked."""
    for item in items:
        product = products[item.product_id]
        item.unit_cost = _cost(product.average_cost)
        record_movement(product, StockMovement.Kind.SALE, -item.quantity, order=order, reference=order.reference)
    OrderItem.objects.bulk_update(items, ["unit_cost"])


def restock_cancelled_order(order, user=None):
    """Put a cancelled order's items back in stock at their sale cost. Safe to call twice."""
    with transaction.atomic():
        if StockMovement.objects.filter(order=order, kind=StockMovement.Kind.RETURN).exists():
            return 0
        items = [i for i in order.items.all() if i.product_id]
        products = lock_products(i.product_id for i in items)
        for item in items:
            product = products[item.product_id]
            cost = item.unit_cost if item.unit_cost is not None else product.average_cost
            record_movement(
                product, StockMovement.Kind.RETURN, item.quantity, cost,
                order=order, reference=order.reference, user=user, note="Order cancelled",
            )
        return len(items)


# ---------------------------------------------------------------- purchasing


def landed_costs(purchase, items):
    """Unit cost per item including its share (by line value) of the purchase's other costs."""
    items_total = sum((i.line_total for i in items), ZERO)
    result = {}
    for item in items:
        share = purchase.other_costs * item.line_total / items_total if items_total else ZERO
        result[item.pk] = _cost(item.unit_cost + share / item.quantity)
    return result


def receive_purchase(purchase, user=None, at=None):
    with transaction.atomic():
        purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
        if not purchase.is_open:
            raise InventoryError(f"{purchase.reference} is {purchase.get_status_display().lower()}; only draft or ordered purchases can be received.")
        items = list(purchase.items.all())
        if not items:
            raise InventoryError(f"{purchase.reference} has no items.")
        costs = landed_costs(purchase, items)
        products = lock_products(i.product_id for i in items)
        for item in items:
            record_movement(
                products[item.product_id], StockMovement.Kind.PURCHASE, item.quantity, costs[item.pk],
                purchase=purchase, reference=purchase.reference, user=user, at=at,
                note=f"From {purchase.supplier.name}",
            )
        purchase.status = Purchase.Status.RECEIVED
        purchase.received_at = at or timezone.now()
        purchase.save(update_fields=["status", "received_at", "updated_at"])
    return purchase


def set_purchase_status(purchase, status):
    """Move an open purchase to Ordered or Cancelled (receiving has its own function)."""
    if status not in (Purchase.Status.ORDERED, Purchase.Status.CANCELLED):
        raise InventoryError("Use receive_purchase() to receive stock.")
    if not purchase.is_open:
        raise InventoryError(f"{purchase.reference} is already {purchase.get_status_display().lower()}.")
    with transaction.atomic():
        purchase.status = status
        purchase.save(update_fields=["status", "updated_at"])
        if status == Purchase.Status.CANCELLED:
            from .payments import release_allocations  # payments imports this module

            release_allocations(purchase)  # any advance paid becomes supplier credit
    return purchase


# ---------------------------------------------------------------- adjustments


def apply_adjustment(adjustment, user=None):
    with transaction.atomic():
        product = lock_products([adjustment.product_id])[adjustment.product_id]
        movement = record_movement(
            product, StockMovement.Kind.ADJUSTMENT, adjustment.quantity, adjustment.unit_cost,
            user=user, reference=adjustment.get_reason_display(), note=adjustment.note,
        )
        adjustment.movement = movement
        adjustment.created_by = user if user and user.is_authenticated else None
        adjustment.save()
    return adjustment


# ---------------------------------------------------------------- reporting


def inventory_summary():
    products = Product.objects.all()
    totals = products.aggregate(
        units=Coalesce(Sum("stock"), 0),
        cost_value=Coalesce(Sum(F("stock") * F("average_cost"), output_field=MONEY_FIELD), ZERO, output_field=MONEY_FIELD),
        retail_value=Coalesce(Sum(F("stock") * F("price"), output_field=MONEY_FIELD), ZERO, output_field=MONEY_FIELD),
    )
    from .payments import supplier_accounts  # payments imports this module

    open_purchases = Purchase.objects.filter(status__in=Purchase.OPEN_STATUSES).prefetch_related("items")
    accounts = supplier_accounts()
    return {
        **totals,
        "potential_profit": totals["retail_value"] - totals["cost_value"],
        "low_stock": products.filter(stock__lte=F("reorder_level")).select_related("category").order_by("stock", "name"),
        "open_purchases": open_purchases.select_related("supplier"),
        "payable": sum((a.owed for a in accounts), ZERO),
        "overdue": sum((a.overdue for a in accounts), ZERO),
    }


def _period_bounds(start, end):
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz),
    )


def _money(value):
    return (value or ZERO).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class PeriodRow:
    label: str
    revenue: Decimal = ZERO
    shipping: Decimal = ZERO
    cogs: Decimal = ZERO
    expenses: Decimal = ZERO
    adjustments: Decimal = ZERO
    other_income: Decimal = ZERO
    orders: int = 0

    @property
    def gross_profit(self):
        return self.revenue + self.shipping - self.cogs

    @property
    def operating_profit(self):
        return self.gross_profit - self.expenses + self.adjustments

    @property
    def net_profit(self):
        return self.operating_profit + self.other_income

    @property
    def total_income(self):
        """Everything earned: product sales + shipping charged + other income."""
        return self.revenue + self.shipping + self.other_income

    @property
    def gross_margin(self):
        income = self.revenue + self.shipping
        return round(self.gross_profit / income * 100, 1) if income else None

    @property
    def net_margin(self):
        income = self.revenue + self.shipping
        return round(self.net_profit / income * 100, 1) if income else None


@dataclass
class ProfitAndLoss:
    start: date
    end: date
    totals: PeriodRow
    months: list
    expenses_by_category: list
    income_by_category: list
    top_products: list
    unknown_cost_items: int
    purchases_received: Decimal
    open_orders: dict = field(default_factory=dict)


def profit_and_loss(start, end):
    """Income statement for orders *delivered* between start and end (inclusive dates).

    Revenue      = product sales of delivered orders (+ shipping charged, shown separately)
    COGS         = Σ unit_cost × qty on those orders (cost snapshot taken at sale time)
    Gross profit = revenue + shipping − COGS
    Operating    = gross profit − operating expenses ± stock adjustments (write-offs / count gains)
    Net profit   = operating profit + other income (Income records, by date)
    Margins are measured against sales revenue (sales + shipping), not other income.
    Purchases are not expenses: bought stock is an asset until sold, then becomes COGS.
    """
    lo, hi = _period_bounds(start, end)
    orders = Order.objects.filter(status=Order.Status.DELIVERED, delivered_at__gte=lo, delivered_at__lt=hi)
    items = OrderItem.objects.filter(order__in=orders)
    line_revenue = ExpressionWrapper(F("unit_price") * F("quantity"), output_field=MONEY_FIELD)
    line_cost = ExpressionWrapper(Coalesce(F("unit_cost"), ZERO) * F("quantity"), output_field=MONEY_FIELD)
    expenses = Expense.objects.filter(date__gte=start, date__lte=end)
    incomes = Income.objects.filter(date__gte=start, date__lte=end)
    adjustments = StockMovement.objects.filter(kind=StockMovement.Kind.ADJUSTMENT, created_at__gte=lo, created_at__lt=hi)
    adjustment_value = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=MONEY_FIELD)

    months = {}

    def month(key):
        key = timezone.localtime(key).date().replace(day=1) if isinstance(key, datetime) else key.replace(day=1)
        return months.setdefault(key, PeriodRow(key.strftime("%b %Y")))

    for row in orders.annotate(m=TruncMonth("delivered_at")).values("m").annotate(
        revenue=Sum("subtotal"), shipping=Sum("shipping_cost"), n=Count("id")
    ).order_by():
        r = month(row["m"]); r.revenue, r.shipping, r.orders = _money(row["revenue"]), _money(row["shipping"]), row["n"]
    for row in items.annotate(m=TruncMonth("order__delivered_at")).values("m").annotate(cogs=Sum(line_cost)).order_by():
        month(row["m"]).cogs = _money(row["cogs"])
    for row in expenses.annotate(m=TruncMonth("date")).values("m").annotate(total=Sum("amount")).order_by():
        month(row["m"]).expenses = _money(row["total"])
    for row in incomes.annotate(m=TruncMonth("date")).values("m").annotate(total=Sum("amount")).order_by():
        month(row["m"]).other_income = _money(row["total"])
    for row in adjustments.annotate(m=TruncMonth("created_at")).values("m").annotate(value=Sum(adjustment_value)).order_by():
        month(row["m"]).adjustments = _money(row["value"])

    totals = PeriodRow("Total")
    for r in months.values():
        totals.revenue += r.revenue
        totals.shipping += r.shipping
        totals.cogs += r.cogs
        totals.expenses += r.expenses
        totals.adjustments += r.adjustments
        totals.other_income += r.other_income
        totals.orders += r.orders

    top_products = list(
        items.values("product_id", "product_name")
        .annotate(units=Sum("quantity"), revenue=Sum(line_revenue), cogs=Sum(line_cost))
        .annotate(profit=F("revenue") - F("cogs"))
        .order_by("-profit")[:10]
    )
    for p in top_products:
        p["margin"] = round(p["profit"] / p["revenue"] * 100, 1) if p["revenue"] else None

    open_orders = Order.objects.exclude(status__in=[Order.Status.DELIVERED, Order.Status.CANCELLED]).aggregate(
        count=Count("id"), value=Coalesce(Sum("subtotal"), ZERO, output_field=MONEY_FIELD)
    )
    purchases_received = sum(
        (p.total for p in Purchase.objects.filter(status=Purchase.Status.RECEIVED, received_at__gte=lo, received_at__lt=hi).prefetch_related("items")),
        ZERO,
    )
    return ProfitAndLoss(
        start=start,
        end=end,
        totals=totals,
        months=[months[k] for k in sorted(months)],
        expenses_by_category=[
            (Expense.Category(row["category"]).label, _money(row["total"]))
            for row in expenses.values("category").annotate(total=Sum("amount")).order_by("-total")
        ],
        income_by_category=[
            (Income.Category(row["category"]).label, _money(row["total"]))
            for row in incomes.values("category").annotate(total=Sum("amount")).order_by("-total")
        ],
        top_products=top_products,
        unknown_cost_items=items.filter(unit_cost__isnull=True).count(),
        purchases_received=_money(purchases_received),
        open_orders=open_orders,
    )
