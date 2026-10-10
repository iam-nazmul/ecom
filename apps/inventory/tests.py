from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.tests import make_product
from apps.orders.models import Order, OrderItem
from apps.orders.tests import ADDRESS

from .models import Expense, Income, Purchase, PurchaseItem, StockAdjustment, StockMovement, Supplier, SupplierPayment
from .payments import CREDIT, PaymentError, apply_credit, delete_payment, record_payment, supplier_account, supplier_statement
from .services import (
    InventoryError, apply_adjustment, landed_costs, profit_and_loss, receive_purchase, record_movement,
    set_purchase_status,
)

D = Decimal


def make_purchase(items, other_costs="0", status=Purchase.Status.ORDERED, supplier=None):
    supplier = supplier or Supplier.objects.get_or_create(name="Acme")[0]
    purchase = Purchase.objects.create(supplier=supplier, status=status, other_costs=D(other_costs))
    for product, qty, cost in items:
        PurchaseItem.objects.create(purchase=purchase, product=product, quantity=qty, unit_cost=D(cost))
    return purchase


def deliver(order, when=None):
    order.status = Order.Status.DELIVERED
    order.save()
    if when:
        Order.objects.filter(pk=order.pk).update(delivered_at=when)
    return order


class CostingTests(TestCase):
    def setUp(self):
        self.product = make_product(price=D("10.00"), stock=0)

    def test_weighted_average_cost(self):
        record_movement(self.product, StockMovement.Kind.PURCHASE, 10, D("4.00"))
        record_movement(self.product, StockMovement.Kind.PURCHASE, 10, D("6.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 20)
        self.assertEqual(self.product.average_cost, D("5.0000"))
        out = record_movement(self.product, StockMovement.Kind.SALE, -5)
        self.assertEqual(out.unit_cost, D("5.0000"))  # outgoing valued at average
        self.assertEqual(out.balance_after, 15)
        record_movement(self.product, StockMovement.Kind.PURCHASE, 5, D("9.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.average_cost, D("6.0000"))  # (15×5 + 5×9) / 20

    def test_cannot_go_negative(self):
        with self.assertRaises(InventoryError):
            record_movement(self.product, StockMovement.Kind.SALE, -1)


class PurchaseTests(TestCase):
    def setUp(self):
        self.a = make_product(name="A", slug="a", stock=0)
        self.b = make_product(name="B", slug="b", stock=0)

    def test_landed_cost_allocation(self):
        purchase = make_purchase([(self.a, 10, "6.00"), (self.b, 5, "8.00")], other_costs="10.00")
        costs = landed_costs(purchase, list(purchase.items.all()))
        a_item, b_item = purchase.items.all()
        # Lines 60 and 40 of 100 → other costs split 6 / 4.
        self.assertEqual(costs[a_item.pk], D("6.6000"))
        self.assertEqual(costs[b_item.pk], D("8.8000"))
        self.assertEqual(purchase.total, D("110.00"))

    def test_receive_adds_stock_and_ledger(self):
        purchase = make_purchase([(self.a, 10, "6.00"), (self.b, 5, "8.00")], other_costs="10.00")
        receive_purchase(purchase)
        self.a.refresh_from_db(); self.b.refresh_from_db(); purchase.refresh_from_db()
        self.assertEqual((self.a.stock, self.b.stock), (10, 5))
        self.assertEqual(self.a.average_cost, D("6.6000"))
        self.assertEqual(purchase.status, Purchase.Status.RECEIVED)
        self.assertIsNotNone(purchase.received_at)
        self.assertEqual(StockMovement.objects.filter(purchase=purchase, kind="purchase").count(), 2)
        with self.assertRaises(InventoryError):
            receive_purchase(purchase)  # not twice
        self.a.refresh_from_db()
        self.assertEqual(self.a.stock, 10)

    def test_cannot_receive_cancelled_or_empty(self):
        with self.assertRaises(InventoryError):
            receive_purchase(make_purchase([(self.a, 1, "1")], status=Purchase.Status.CANCELLED))
        with self.assertRaises(InventoryError):
            receive_purchase(make_purchase([]))

    def test_payment_status(self):
        purchase = make_purchase([(self.a, 10, "5.00")])
        self.assertEqual((purchase.payment_status, purchase.balance_due), ("Unpaid", D("50.00")))
        purchase.amount_paid = D("20")
        self.assertEqual((purchase.payment_status, purchase.balance_due), ("Partly paid", D("30.00")))
        purchase.amount_paid = D("50")
        self.assertEqual((purchase.payment_status, purchase.balance_due), ("Paid", D("0")))


class SalesTests(TestCase):
    def setUp(self):
        self.product = make_product(price=D("10.00"), stock=0)
        record_movement(self.product, StockMovement.Kind.PURCHASE, 5, D("6.00"))

    def checkout(self, qty):
        self.client.post(reverse("cart:add", args=[self.product.pk]), {"quantity": qty})
        self.client.post(reverse("orders:checkout"), ADDRESS)
        return Order.objects.latest("pk")

    def test_checkout_records_cost_and_sale_movement(self):
        order = self.checkout(2)
        item = order.items.get()
        self.assertEqual(item.unit_cost, D("6.0000"))
        sale = StockMovement.objects.get(order=order)
        self.assertEqual((sale.kind, sale.quantity, sale.balance_after), ("sale", -2, 3))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_cancel_restocks_once(self):
        order = self.checkout(2)
        for _ in range(2):
            order.status = Order.Status.CANCELLED
            order.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(StockMovement.objects.filter(order=order, kind="return").count(), 1)

    def test_cancelled_order_cannot_be_reopened_in_admin(self):
        order = self.checkout(1)
        order.status = Order.Status.CANCELLED
        order.save()
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        response = self.client.post(reverse("admin:orders_order_changelist"), {
            "action": "", "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(order.pk), "form-0-status": Order.Status.PENDING, "_save": "Save",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "can&#x27;t be reopened")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_delivered_at_set_and_cleared(self):
        order = self.checkout(1)
        self.assertIsNone(order.delivered_at)
        deliver(order)
        self.assertIsNotNone(order.delivered_at)
        order.status = Order.Status.SHIPPED
        order.save()
        self.assertIsNone(order.delivered_at)


class AdjustmentTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=0)
        record_movement(self.product, StockMovement.Kind.PURCHASE, 10, D("5.00"))
        self.admin = User.objects.create_superuser("root", "r@example.com", "pw")

    def test_write_off_valued_at_average(self):
        adj = apply_adjustment(StockAdjustment(product=self.product, quantity=-2, reason="damaged"), user=self.admin)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(adj.movement.value, D("-10.00"))
        self.assertEqual(adj.created_by, self.admin)

    def test_admin_rejects_removing_more_than_stock(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:inventory_stockadjustment_add"), {
            "product": self.product.pk, "quantity": "-11", "reason": "lost", "unit_cost": "", "note": "",
        })
        self.assertContains(response, "Only 10 in stock")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_admin_adjustment_creates_movement(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:inventory_stockadjustment_add"), {
            "product": self.product.pk, "quantity": "10", "reason": "count", "unit_cost": "7.00", "note": "Recount",
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual((self.product.stock, self.product.average_cost), (20, D("6.0000")))

    def test_product_admin_stock_is_read_only(self):
        self.client.force_login(self.admin)
        url = reverse("admin:catalog_product_change", args=[self.product.pk])
        html = self.client.get(url).content.decode()
        self.assertNotIn('name="stock"', html)
        self.assertNotIn('name="average_cost"', html)


class ProfitAndLossTests(TestCase):
    def setUp(self):
        self.product = make_product(price=D("10.00"), stock=0)
        record_movement(self.product, StockMovement.Kind.PURCHASE, 20, D("6.00"))
        self.today = timezone.localdate()

    def order(self, qty, status=Order.Status.PENDING, unit_cost=D("6.00"), shipping=D("5.00")):
        order = Order.objects.create(
            full_name="A", email="a@example.com", address_line1="1", city="Dhaka", postal_code="1", country="BD",
            subtotal=self.product.price * qty, shipping_cost=shipping, total=self.product.price * qty + shipping, status=status,
        )
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name,
                                 unit_price=self.product.price, unit_cost=unit_cost, quantity=qty)
        return order

    def test_statement(self):
        deliver(self.order(2))                                   # revenue 20, ship 5, cogs 12
        self.order(3)                                            # pending: not counted
        deliver(self.order(4), when=timezone.now() - timedelta(days=400))  # outside period
        Expense.objects.create(date=self.today, category="rent", description="Rent", amount=D("3.00"))
        apply_adjustment(StockAdjustment(product=self.product, quantity=-1, reason="damaged"))  # −6
        report = profit_and_loss(self.today - timedelta(days=30), self.today)
        t = report.totals
        self.assertEqual((t.revenue, t.shipping, t.cogs), (D("20.00"), D("5.00"), D("12.00")))
        self.assertEqual(t.gross_profit, D("13.00"))
        self.assertEqual(t.expenses, D("3.00"))
        self.assertEqual(t.adjustments, D("-6.00"))
        self.assertEqual(t.operating_profit, D("4.00"))
        self.assertEqual(t.net_profit, D("4.00"))
        self.assertEqual(t.orders, 1)
        self.assertEqual(report.open_orders["count"], 1)
        self.assertEqual(report.top_products[0]["profit"], D("8"))
        self.assertEqual(report.expenses_by_category, [("Rent", D("3.00"))])

    def test_other_income(self):
        deliver(self.order(2))                                   # gross 13
        Expense.objects.create(date=self.today, category="rent", description="Rent", amount=D("20.00"))
        Income.objects.create(date=self.today, category="rebate", description="Rebate", amount=D("9.50"))
        Income.objects.create(date=self.today, category="services", description="Wrap", amount=D("2.50"))
        Income.objects.create(date=self.today - timedelta(days=400), category="other", description="Old", amount=D("99"))
        t = profit_and_loss(self.today - timedelta(days=30), self.today).totals
        self.assertEqual(t.operating_profit, D("-7.00"))
        self.assertEqual(t.other_income, D("12.00"))
        self.assertEqual(t.net_profit, D("5.00"))
        self.assertEqual(t.total_income, D("37.00"))           # 20 sales + 5 shipping + 12 other
        self.assertEqual(t.net_margin, D("20.0"))              # margin vs sales revenue (25)
        report = profit_and_loss(self.today - timedelta(days=30), self.today)
        self.assertEqual(report.income_by_category[0], ("Supplier rebates & discounts", D("9.50")))

    def test_income_only_month_appears(self):
        Income.objects.create(date=self.today, category="interest", description="Bank interest", amount=D("3.00"))
        report = profit_and_loss(self.today, self.today)
        self.assertEqual([m.other_income for m in report.months], [D("3.00")])
        self.assertEqual(report.totals.net_profit, D("3.00"))

    def test_unknown_cost_flagged(self):
        deliver(self.order(1, unit_cost=None))
        report = profit_and_loss(self.today, self.today)
        self.assertEqual(report.unknown_cost_items, 1)
        self.assertEqual(report.totals.cogs, D("0"))

    def test_months(self):
        deliver(self.order(1), when=timezone.now() - timedelta(days=40))
        deliver(self.order(1))
        report = profit_and_loss(self.today - timedelta(days=60), self.today)
        self.assertGreaterEqual(len(report.months), 2)
        self.assertEqual(sum(m.orders for m in report.months), 2)


class StaffPagesTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=0)
        self.purchase = make_purchase([(self.product, 4, "3.00")])
        self.urls = [
            reverse("inventory:dashboard"),
            reverse("inventory:profit_loss"),
            reverse("inventory:profit_loss") + "?range=last_month",
            reverse("inventory:profit_loss") + "?start=2026-01-01&end=2026-03-31",
            reverse("inventory:ledger", args=[self.product.pk]),
        ]

    def test_staff_only(self):
        for url in self.urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(User.objects.create_user("shopper", password="pw"))
        for url in self.urls:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_pages_render_for_superuser(self):
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        for url in self.urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(self.urls[0]), self.purchase.reference)
        self.assertContains(self.client.get(self.urls[0]), "Profit &amp; loss")

    def test_profit_loss_superuser_only(self):
        self.client.force_login(User.objects.create_user("clerk", password="pw", is_staff=True))
        dashboard = self.client.get(reverse("inventory:dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, reverse("inventory:profit_loss"))  # no tab
        self.assertEqual(self.client.get(reverse("inventory:ledger", args=[self.product.pk])).status_code, 200)
        for url in self.urls[1:4]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)
            self.assertContains(response, "Only superusers can view this report.", status_code=403)

    def test_receive_from_dashboard(self):
        self.client.force_login(User.objects.create_user("clerk", password="pw", is_staff=True))
        self.client.post(reverse("inventory:receive", args=[self.purchase.pk]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
        response = self.client.post(reverse("inventory:receive", args=[self.purchase.pk]), follow=True)
        self.assertContains(response, "only draft or ordered purchases can be received")

    def test_admin_pages(self):
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        for name in ["supplier", "purchase", "stockmovement", "stockadjustment", "expense", "income"]:
            self.assertEqual(self.client.get(reverse(f"admin:inventory_{name}_changelist")).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("admin:inventory_purchase_change", args=[self.purchase.pk])).status_code, 200)

    def test_admin_receive_action(self):
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        self.client.post(reverse("admin:inventory_purchase_changelist"),
                         {"action": "receive", "_selected_action": [self.purchase.pk]})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)


class SeedTests(TestCase):
    def test_seed_inventory_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            call_command("seed_demo", no_images=True, stdout=StringIO())
        counts = (Supplier.objects.count(), Purchase.objects.count(), Expense.objects.count(), Income.objects.count(), StockMovement.objects.count())
        self.assertTrue(all(counts))
        self.assertFalse(Product.objects.filter(average_cost=0).exists())
        self.assertTrue(Purchase.objects.filter(status="ordered").exists())
        # Ledger and stock agree for every product.
        for product in Product.objects.all():
            last = product.stock_movements.order_by("-created_at", "-pk").first()
            total = sum(product.stock_movements.values_list("quantity", flat=True))
            self.assertEqual(total, product.stock, product)
            self.assertIsNotNone(last)
        self.assertTrue(SupplierPayment.objects.exists())
        for purchase in Purchase.objects.all():  # cached amount_paid == allocations
            self.assertEqual(purchase.amount_paid, sum(purchase.allocations.values_list("amount", flat=True)) or 0)
        report = profit_and_loss(timezone.localdate() - timedelta(days=120), timezone.localdate())
        self.assertGreater(report.totals.revenue, 0)
        self.assertEqual(report.unknown_cost_items, 0)
        with self.captureOnCommitCallbacks(execute=True):
            call_command("seed_demo", no_images=True, stdout=StringIO())
        self.assertEqual(
            counts, (Supplier.objects.count(), Purchase.objects.count(), Expense.objects.count(), Income.objects.count(), StockMovement.objects.count())
        )


class SupplierPaymentTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Acme", payment_terms_days=10)
        self.product = make_product(stock=0)
        self.old = self.received([(self.product, 10, "10.00")], days_ago=20)  # total 100, due 10 days ago
        self.new = self.received([(self.product, 5, "10.00")], days_ago=2)    # total 50, due in 8 days

    def received(self, items, days_ago):
        purchase = make_purchase(items, supplier=self.supplier)
        receive_purchase(purchase, at=timezone.now() - timedelta(days=days_ago))
        purchase.refresh_from_db()
        return purchase

    def refresh(self):
        self.old.refresh_from_db(); self.new.refresh_from_db()

    def test_auto_allocates_oldest_first_and_keeps_credit(self):
        payment = record_payment(self.supplier, D("170"))
        self.refresh()
        self.assertEqual((self.old.amount_paid, self.new.amount_paid), (D("100"), D("50")))
        self.assertEqual(payment.unallocated, D("20"))
        account = supplier_account(self.supplier)
        self.assertEqual((account.billed, account.paid, account.owed, account.credit), (D("150"), D("170"), D("0"), D("20")))

    def test_partial_payment_and_overdue(self):
        record_payment(self.supplier, D("60"))
        self.refresh()
        self.assertEqual((self.old.amount_paid, self.old.payment_status), (D("60"), "Partly paid"))
        self.assertTrue(self.old.is_overdue)
        self.assertFalse(self.new.is_overdue)
        account = supplier_account(self.supplier)
        self.assertEqual((account.owed, account.overdue), (D("90"), D("40")))

    def test_specific_purchase_then_excess_oldest_first(self):
        record_payment(self.supplier, D("80"), allocate_to=self.new)
        self.refresh()
        self.assertEqual((self.new.amount_paid, self.old.amount_paid), (D("50"), D("30")))

    def test_keep_as_credit_then_apply(self):
        payment = record_payment(self.supplier, D("120"), allocate_to=CREDIT)
        self.assertEqual(payment.unallocated, D("120"))
        self.assertEqual(apply_credit(self.supplier), D("120"))
        self.refresh()
        self.assertEqual((self.old.amount_paid, self.new.amount_paid), (D("100"), D("20")))
        self.assertEqual(apply_credit(self.supplier), D("0"))

    def test_auto_does_not_prepay_open_orders(self):
        open_po = make_purchase([(self.product, 1, "40.00")], supplier=self.supplier)
        record_payment(self.supplier, D("200"))
        open_po.refresh_from_db()
        self.assertEqual(open_po.amount_paid, D("0"))
        record_payment(self.supplier, D("10"), allocate_to=open_po)  # explicit advance
        open_po.refresh_from_db()
        self.assertEqual(open_po.amount_paid, D("10"))

    def test_cancelling_open_po_turns_advance_into_credit(self):
        open_po = make_purchase([(self.product, 1, "40.00")], supplier=self.supplier)
        payment = record_payment(self.supplier, D("40"), allocate_to=open_po)
        set_purchase_status(open_po, Purchase.Status.CANCELLED)
        open_po.refresh_from_db()
        self.assertEqual(open_po.amount_paid, D("0"))
        self.assertEqual(SupplierPayment.objects.get(pk=payment.pk).unallocated, D("40"))

    def test_delete_payment_restores_balances(self):
        payment = record_payment(self.supplier, D("150"))
        delete_payment(payment)
        self.refresh()
        self.assertEqual((self.old.amount_paid, self.new.amount_paid), (D("0"), D("0")))

    def test_rejects_bad_payments(self):
        other = make_purchase([(self.product, 1, "1")], supplier=Supplier.objects.create(name="Other"))
        for kwargs in ({"amount": D("0")}, {"amount": D("5"), "allocate_to": other}):
            with self.assertRaises(PaymentError):
                record_payment(self.supplier, kwargs.pop("amount"), **kwargs)
        self.assertFalse(SupplierPayment.objects.exists())

    def test_statement_running_balance(self):
        record_payment(self.supplier, D("100"), date=timezone.localdate() - timedelta(days=5))
        lines = supplier_statement(self.supplier)
        self.assertEqual([(l.kind, l.balance) for l in lines], [("bill", D("100")), ("payment", D("0")), ("bill", D("50"))])

    def test_dashboard_payable_counts_received_only(self):
        make_purchase([(self.product, 1, "999.00")], supplier=self.supplier)  # open PO, not owed yet
        record_payment(self.supplier, D("30"))
        from .services import inventory_summary
        summary = inventory_summary()
        self.assertEqual(summary["payable"], D("120"))
        self.assertEqual(summary["overdue"], D("70"))


class SupplierPaymentPagesTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("clerk", password="pw", is_staff=True)
        self.supplier = Supplier.objects.create(name="Acme", payment_terms_days=0)
        self.product = make_product(stock=0)
        self.purchase = make_purchase([(self.product, 10, "10.00")], supplier=self.supplier)
        receive_purchase(self.purchase)
        self.url = reverse("inventory:supplier", args=[self.supplier.pk])

    def test_staff_only(self):
        for url in (reverse("inventory:suppliers"), self.url):
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_summary_cards_superuser_only(self):
        pages = [(reverse("inventory:suppliers"), "Owed to suppliers"), (reverse("inventory:dashboard"), "Stock value (cost)")]
        self.client.force_login(self.staff)
        for url, text in pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, text)
            self.assertNotContains(response, "Owed to suppliers")
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        for url, text in pages:
            self.assertContains(self.client.get(url), text)

    def test_list_and_detail_render(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("inventory:suppliers")), "100.00 owed")
        response = self.client.get(self.url)
        self.assertContains(response, self.purchase.reference)
        self.assertContains(response, "Record a payment")

    def test_record_payment_from_page(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {
            "amount": "40", "date": timezone.localdate().isoformat(), "method": "bkash",
            "reference": "TX1", "note": "", "allocate_to": str(self.purchase.pk),
        }, follow=True)
        self.assertContains(response, "applied to purchases")
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.amount_paid, D("40"))
        self.assertEqual(SupplierPayment.objects.get().created_by, self.staff)

    def test_invalid_payment_form(self):
        self.client.force_login(self.staff)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = self.client.post(self.url, {"amount": "-5", "date": tomorrow, "method": "cash", "allocate_to": "auto"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "be in the future")
        self.assertFalse(SupplierPayment.objects.exists())

    def test_apply_credit_button(self):
        record_payment(self.supplier, D("30"), allocate_to=CREDIT)
        self.client.force_login(self.staff)
        self.client.post(reverse("inventory:apply_credit", args=[self.supplier.pk]))
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.amount_paid, D("30"))

    def test_admin_add_and_delete_payment(self):
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        response = self.client.post(reverse("admin:inventory_supplierpayment_add"), {
            "supplier": self.supplier.pk, "date": timezone.localdate().isoformat(), "amount": "25.00",
            "method": "cash", "reference": "", "note": "", "allocate_to": "",
        })
        self.assertEqual(response.status_code, 302)
        payment = SupplierPayment.objects.get()
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.amount_paid, D("25"))
        self.client.post(reverse("admin:inventory_supplierpayment_delete", args=[payment.pk]), {"post": "yes"})
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.amount_paid, D("0"))
        for name in ("supplier", "supplierpayment"):
            self.assertEqual(self.client.get(reverse(f"admin:inventory_{name}_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:inventory_purchase_change", args=[self.purchase.pk])).status_code, 200)
