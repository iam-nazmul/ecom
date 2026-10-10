"""Demo inventory data for `seed_demo`: product costs, suppliers, purchases and expenses.

Idempotent: costs are only set on demo products that have none, and suppliers, purchases
and expenses are only created when no supplier exists yet.
"""
import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem

from .models import Expense, Income, Purchase, PurchaseItem, StockMovement, Supplier
from .payments import record_payment
from .services import receive_purchase, record_opening_balance

DEMO_ORDER_NOTE = "Demo order (seed_demo)."
# name, contact, phone, category, payment terms (days), usual payment method
SUPPLIERS = [
    ("Dhaka Electronics Wholesale", "Karim Uddin", "+880 1711-000101", "electronics", 30, "bank"),
    ("Fashion Hub Garments", "Shirin Akter", "+880 1811-000202", "clothing", 15, "bkash"),
    ("HomeCraft Traders", "Jamal Hossain", "+880 1911-000303", "home-kitchen", 7, "nagad"),
    ("Bookworm Distributors", "Farhana Islam", "+880 1611-000404", "books", 0, "cash"),
]
MONTHLY_EXPENSES = [
    (1, Expense.Category.RENT, "Warehouse & office rent", "120.00", "Gulshan Properties"),
    (5, Expense.Category.UTILITIES, "Electricity and internet", "25.00", "DESCO / Link3"),
    (10, Expense.Category.MARKETING, "Facebook & Google ads", "40.00", "Meta / Google"),
    (15, Expense.Category.PACKAGING, "Boxes, tape and labels", "15.00", "Packaging BD"),
    (20, Expense.Category.DELIVERY, "Courier charges", "30.00", "Pathao Courier"),
]

MONTHLY_INCOME = [
    (8, Income.Category.SERVICES, "Gift wrapping & setup service", "18.00", "Store customers"),
    (18, Income.Category.COMMISSION, "Affiliate commission", "12.00", "Daraz Affiliate"),
    (28, Income.Category.REBATE, "Supplier volume rebate", "25.00", "Dhaka Electronics Wholesale"),
]


def _aware(day, hour=11):
    return timezone.make_aware(datetime.combine(day, time(hour)))


def seed_costs(demo_slugs, rng):
    """Give demo products a realistic cost (50–68% of price) and value their opening stock at it."""
    updated = 0
    for product in Product.objects.filter(slug__in=demo_slugs, average_cost=0):
        cost = (product.price * Decimal(str(round(rng.uniform(0.50, 0.68), 2)))).quantize(Decimal("0.01"))
        if product.stock_movements.exists():
            product.average_cost = cost
            product.save(update_fields=["average_cost", "updated_at"])
            product.stock_movements.filter(kind=StockMovement.Kind.OPENING).update(unit_cost=cost)
        else:
            record_opening_balance(product, cost)
        updated += 1
    return updated


def backfill_demo_orders(rng):
    """Demo orders: cost of goods sold from current costs, deliveries spread over ~3 months."""
    for item in OrderItem.objects.filter(order__notes=DEMO_ORDER_NOTE, unit_cost__isnull=True).select_related("product"):
        if item.product:
            item.unit_cost = item.product.average_cost
            item.save(update_fields=["unit_cost"])
    today = timezone.localdate()
    for order in Order.objects.filter(notes=DEMO_ORDER_NOTE, status=Order.Status.DELIVERED):
        days_ago = random.Random(order.reference).randint(2, 85)
        delivered = _aware(today - timedelta(days=days_ago), hour=15)
        Order.objects.filter(pk=order.pk).update(delivered_at=delivered, created_at=delivered - timedelta(days=3))


def seed_purchasing(rng):
    if Supplier.objects.exists():
        return 0
    today = timezone.localdate()
    created = 0
    for index, (name, contact, phone, category_slug, terms, method) in enumerate(SUPPLIERS):
        supplier = Supplier.objects.create(
            name=name, contact_name=contact, phone=phone, payment_terms_days=terms,
            email=f"orders@{name.split()[0].lower()}.example.com", address="Dhaka, Bangladesh",
        )
        products = list(Product.objects.filter(category__slug=category_slug).order_by("pk"))
        if not products:
            continue
        plans = [(70 - index * 6, Purchase.Status.RECEIVED), (28 - index * 3, Purchase.Status.RECEIVED)]
        if index == 0:
            plans.append((3, Purchase.Status.ORDERED))
        if index == 1:
            plans.append((1, Purchase.Status.DRAFT))
        for days_ago, status in plans:
            order_date = today - timedelta(days=days_ago)
            with transaction.atomic():
                purchase = Purchase.objects.create(
                    supplier=supplier, status=Purchase.Status.ORDERED, order_date=order_date,
                    expected_date=order_date + timedelta(days=4), invoice_number=f"INV-{rng.randint(10000, 99999)}",
                    other_costs=Decimal(rng.choice(["10.00", "15.00", "20.00", "25.00"])),
                )
                for product in rng.sample(products, k=min(len(products), rng.randint(2, 3))):
                    base = product.average_cost or product.price * Decimal("0.6")
                    PurchaseItem.objects.create(
                        purchase=purchase, product=product, quantity=rng.choice([6, 8, 10, 12, 15]),
                        unit_cost=(base * Decimal(str(round(rng.uniform(0.95, 1.05), 2)))).quantize(Decimal("0.01")),
                    )
                if status == Purchase.Status.RECEIVED:
                    receive_purchase(purchase, at=_aware(order_date + timedelta(days=3)))
                    purchase.refresh_from_db()
                    # Paid in full a few days after delivery, except HomeCraft's latest delivery:
                    # half paid and past its 7-day terms, so the pages show a balance and an overdue bill.
                    part_paid = name == "HomeCraft Traders" and days_ago < 30
                    amount = (purchase.total / 2).quantize(Decimal("0.01")) if part_paid else purchase.total
                    record_payment(
                        supplier, amount, date=min(order_date + timedelta(days=5), today), method=method,
                        reference=f"TXN{rng.randint(100000, 999999)}", allocate_to=purchase,
                    )
                else:
                    purchase.status = status
                    purchase.save(update_fields=["status"])
                    if status == Purchase.Status.ORDERED:  # 30% advance on the open order
                        record_payment(supplier, (purchase.total * Decimal("0.3")).quantize(Decimal("0.01")),
                                       date=order_date, method=method, note="Advance", allocate_to=purchase)
            created += 1
    return created


def _monthly(model, rows, party_field):
    """Create `rows` (day, category, description, amount, party) for this and the previous two months."""
    if model.objects.exists():
        return 0
    today = timezone.localdate()
    month = (today.replace(day=1) - timedelta(days=62)).replace(day=1)
    created = 0
    while month <= today:
        for day, category, description, amount, party in rows:
            when = month.replace(day=day)
            if when <= today:
                model.objects.create(date=when, category=category, description=description,
                                     amount=Decimal(amount), **{party_field: party})
                created += 1
        month = (month + timedelta(days=32)).replace(day=1)
    return created


def seed_expenses():
    return _monthly(Expense, MONTHLY_EXPENSES, "paid_to")


def seed_income():
    return _monthly(Income, MONTHLY_INCOME, "received_from")


def seed_inventory(demo_slugs):
    rng = random.Random("inventory-demo")
    costs = seed_costs(demo_slugs, rng)
    backfill_demo_orders(rng)
    purchases = seed_purchasing(rng)
    expenses = seed_expenses()
    income = seed_income()
    return {"costs": costs, "purchases": purchases, "expenses": expenses, "income": income}
