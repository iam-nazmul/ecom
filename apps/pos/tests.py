import unittest

from django.conf import settings

if not settings.POS_ENABLED:  # the app isn't installed: its models can't even be imported
    raise unittest.SkipTest("POS_ENABLED is off")

import json  # noqa: E402
from datetime import timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

from django.contrib.auth.models import Permission, User  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.catalog.tests import make_product  # noqa: E402
from apps.inventory.models import StockMovement  # noqa: E402
from apps.inventory.services import profit_and_loss, record_movement  # noqa: E402
from apps.orders.models import Order  # noqa: E402

from .models import Barcode, CashMovement, Register, RegisterSession, Sale  # noqa: E402
from .services import (  # noqa: E402
    PosError, add_cash_movement, checkout, close_session, open_session, session_summary, void_sale,
)

D = Decimal


@override_settings(POS_MAX_DISCOUNT_PERCENT=D("20"))
class PosTestCase(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user("cashier", password="pw", is_staff=True)
        self.boss = User.objects.create_superuser("boss", "b@example.com", "pw")
        self.register = Register.objects.create(name="Counter 1")
        self.session = open_session(self.register, self.cashier, D("100"))
        self.pen = make_product(name="Pen", slug="pen", price=D("10.00"), stock=0)
        record_movement(self.pen, StockMovement.Kind.PURCHASE, 20, D("4.00"))
        self.book = make_product(name="Book", slug="book", price=D("50.00"), stock=0)
        record_movement(self.book, StockMovement.Kind.PURCHASE, 5, D("30.00"))

    def sell(self, lines, payments, user=None, **kwargs):
        return checkout(self.session, user or self.cashier, lines, payments, **kwargs)


class SessionTests(PosTestCase):
    def test_one_open_session_per_register_and_user(self):
        with self.assertRaises(PosError):
            open_session(self.register, self.boss)  # register busy
        with self.assertRaises(PosError):
            open_session(Register.objects.create(name="Counter 2"), self.cashier)  # user busy

    def test_close_with_cash_count(self):
        self.sell([(self.pen.pk, 3)], [("cash", D("50"))])            # 30 cash, 20 change
        self.sell([(self.book.pk, 1)], [("card", D("50"), "TX9")])    # card
        add_cash_movement(self.session, CashMovement.Kind.OUT, D("15"), "Tea", self.cashier)
        add_cash_movement(self.session, CashMovement.Kind.IN, D("5"), "Coins", self.cashier)
        summary = session_summary(self.session)
        self.assertEqual(summary["expected_cash"], D("120.00"))  # 100 + 30 + 5 - 15
        self.assertEqual(dict(summary["by_method"])["Card"], D("50.00"))
        closed = close_session(self.session, self.cashier, D("118"))
        self.assertEqual((closed.expected_cash, closed.difference), (D("120.00"), D("-2.00")))
        with self.assertRaises(PosError):
            self.sell([(self.pen.pk, 1)], [("cash", D("10"))])

    def test_cash_out_cannot_exceed_drawer(self):
        with self.assertRaises(PosError):
            add_cash_movement(self.session, CashMovement.Kind.OUT, D("101"), "Too much", self.cashier)


class CheckoutTests(PosTestCase):
    def test_sale_updates_stock_ledger_and_profit(self):
        sale = self.sell([(self.pen.pk, 2), (self.book.pk, 1), (self.pen.pk, 1)], [("cash", D("100"))])
        self.assertEqual((sale.subtotal, sale.total, sale.change_given), (D("80.00"), D("80.00"), D("20.00")))
        self.assertEqual(sale.lines.get(product=self.pen).quantity, 3)  # duplicate lines merged
        self.pen.refresh_from_db()
        self.assertEqual(self.pen.stock, 17)
        order = sale.order
        self.assertEqual((order.status, order.total, order.shipping_cost), (Order.Status.DELIVERED, D("80.00"), D("0")))
        self.assertIsNotNone(order.delivered_at)
        self.assertEqual(StockMovement.objects.filter(order=order, kind="sale").count(), 2)
        report = profit_and_loss(timezone.localdate(), timezone.localdate())
        self.assertEqual((report.totals.revenue, report.totals.cogs), (D("80.00"), D("42.00")))  # 3×4 + 30

    def test_discount_and_split_payment(self):
        sale = self.sell([(self.book.pk, 2)], [("bkash", D("50"), "BK1"), ("cash", D("40"))], discount_percent=D("10"))
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total), (D("100.00"), D("10.00"), D("90.00")))
        cash = sale.payments.get(method="cash")
        self.assertEqual((cash.amount, cash.received, sale.change_given), (D("40.00"), D("40.00"), D("0.00")))
        self.assertEqual(sale.lines.get().unit_price, D("45.00"))
        self.assertEqual(sale.order.items.get().unit_price, D("45.00"))

    def test_discount_limit(self):
        with self.assertRaises(PosError):
            self.sell([(self.pen.pk, 1)], [("cash", D("10"))], discount_percent=D("25"))
        boss_session = open_session(Register.objects.create(name="Back office"), self.boss)
        sale = checkout(boss_session, self.boss, [(self.pen.pk, 1)], [], discount_percent=D("100"))
        self.assertEqual(sale.total, D("0.00"))

    def items_total(self, sale):
        return sum((i.unit_price * i.quantity for i in sale.order.items.all()), D("0"))

    def test_discount_by_amount(self):
        sale = self.sell([(self.pen.pk, 3)], [("cash", D("29"))], discount_amount=D("1"))
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.discount_percent, sale.total),
                         (D("30.00"), D("1.00"), D("3.33"), D("29.00")))
        # 29.00 / 3 doesn't divide evenly: the order gets 9.66 + 2 × 9.67 so it still adds up.
        self.assertEqual(sorted((i.quantity, i.unit_price) for i in sale.order.items.all()),
                         [(1, D("9.66")), (2, D("9.67"))])
        self.assertEqual(self.items_total(sale), D("29.00"))
        self.pen.refresh_from_db()
        self.assertEqual(self.pen.stock, 17)
        self.assertEqual(profit_and_loss(timezone.localdate(), timezone.localdate()).totals.revenue, D("29.00"))

    def test_discount_amount_limits(self):
        for amount in (D("7"), D("-1")):  # 7 > 20% of 30
            with self.assertRaises(PosError):
                self.sell([(self.pen.pk, 3)], [("cash", D("30"))], discount_amount=amount)
        boss_session = open_session(Register.objects.create(name="Back office"), self.boss)
        with self.assertRaises(PosError):
            checkout(boss_session, self.boss, [(self.pen.pk, 1)], [], discount_amount=D("11"))
        self.assertEqual(checkout(boss_session, self.boss, [(self.pen.pk, 1)], [], discount_amount=D("10")).total, D("0.00"))

    def test_adjustment_reduces_amount_due_and_revenue(self):
        sale = self.sell([(self.pen.pk, 3), (self.book.pk, 1)], [("cash", D("70"))],
                         discount_percent=D("10"), adjustment=D("2"))
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.adjustment, sale.total, sale.change_given),
                         (D("80.00"), D("8.00"), D("2.00"), D("70.00"), D("0.00")))
        self.assertEqual((sale.order.total, self.items_total(sale)), (D("70.00"), D("70.00")))
        report = profit_and_loss(timezone.localdate(), timezone.localdate())
        self.assertEqual(report.totals.revenue, D("70.00"))
        summary = session_summary(self.session)
        self.assertEqual((summary["gross"], summary["discounts"], summary["adjustments"], summary["net"]),
                         (D("80.00"), D("8.00"), D("2.00"), D("70.00")))
        self.assertEqual(summary["expected_cash"], D("170.00"))  # only the cash actually taken

    def test_adjustment_is_never_given_as_change(self):
        with self.assertRaises(PosError):
            self.sell([(self.book.pk, 1)], [("cash", D("50"))], adjustment=D("2"))  # would give 2 change
        sale = self.sell([(self.book.pk, 1)], [("bkash", D("20"), "BK"), ("cash", D("28"))], adjustment=D("2"))
        self.assertEqual((sale.total, sale.change_given), (D("48.00"), D("0.00")))

    @override_settings(POS_MAX_ADJUSTMENT=D("5"))
    def test_adjustment_limits(self):
        for amount in (D("6"), D("-1")):
            with self.assertRaises(PosError):
                self.sell([(self.book.pk, 1)], [("cash", D("44"))], adjustment=amount)
        boss_session = open_session(Register.objects.create(name="Back office"), self.boss)
        with self.assertRaises(PosError):  # more than the amount due
            checkout(boss_session, self.boss, [(self.pen.pk, 1)], [], discount_percent=D("50"), adjustment=D("6"))
        sale = checkout(boss_session, self.boss, [(self.book.pk, 1)], [("cash", D("40"))], adjustment=D("10"))
        self.assertEqual(sale.total, D("40.00"))

    def test_rejections_write_nothing(self):
        cases = [
            ([(self.pen.pk, 1)], [("cash", D("5"))]),                  # short
            ([(self.pen.pk, 1)], [("card", D("11"))]),                 # card over total
            ([(self.book.pk, 6)], [("cash", D("500"))]),               # stock
            ([], [("cash", D("1"))]),                                  # empty
            ([(self.pen.pk, 1)], [("cheque", D("10"))]),               # unknown method
        ]
        for lines, payments in cases:
            with self.assertRaises(PosError):
                self.sell(lines, payments)
        self.pen.is_active = False
        self.pen.save()
        with self.assertRaises(PosError):
            self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock, 5)


class VoidTests(PosTestCase):
    def test_void_restocks_and_leaves_profit(self):
        sale = self.sell([(self.book.pk, 2)], [("cash", D("100"))])
        with self.assertRaises(PosError):
            void_sale(sale, self.cashier, "Wrong item")  # no permission
        void_sale(sale, self.boss, "Wrong item")
        sale.refresh_from_db(); self.book.refresh_from_db()
        self.assertTrue(sale.is_voided)
        self.assertEqual(self.book.stock, 5)
        self.assertEqual(sale.order.status, Order.Status.CANCELLED)
        self.assertEqual(profit_and_loss(timezone.localdate(), timezone.localdate()).totals.revenue, D("0"))
        summary = session_summary(self.session)
        self.assertEqual((summary["sales_count"], summary["voided_count"], summary["expected_cash"]), (0, 1, D("100.00")))
        with self.assertRaises(PosError):
            void_sale(sale, self.boss, "again")

    def test_cashier_with_permission_can_void(self):
        self.cashier.user_permissions.add(Permission.objects.get(codename="void_sale"))
        self.cashier = User.objects.get(pk=self.cashier.pk)  # reset permission cache
        sale = self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        void_sale(sale, self.cashier, "Customer changed mind")
        self.assertTrue(Sale.objects.get(pk=sale.pk).is_voided)

    def test_cannot_void_after_close(self):
        sale = self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        close_session(self.session, self.cashier, D("110"))
        with self.assertRaises(PosError):
            void_sale(sale, self.boss, "late")


class PosViewTests(PosTestCase):
    def test_staff_only(self):
        self.assertEqual(self.client.get(reverse("pos:terminal")).status_code, 302)
        self.client.force_login(User.objects.create_user("shopper", password="pw"))
        self.assertEqual(self.client.get(reverse("pos:terminal")).status_code, 302)

    def test_open_register_flow(self):
        clerk = User.objects.create_user("clerk", password="pw", is_staff=True)
        self.client.force_login(clerk)
        response = self.client.get(reverse("pos:terminal"))
        self.assertContains(response, "Open a register")
        self.assertContains(response, "In use")  # Counter 1 is open by the cashier
        register = Register.objects.create(name="Counter 2")
        self.client.post(reverse("pos:open"), {"register": register.pk, "opening_cash": "50"})
        self.assertContains(self.client.get(reverse("pos:terminal")), 'id="pos"')

    def test_product_api_search_and_barcode(self):
        Barcode.objects.create(product=self.pen, code="8901234567890")
        self.client.force_login(self.cashier)
        data = self.client.get(reverse("pos:api_products"), {"q": "8901234567890"}).json()
        self.assertEqual(data["exact"]["id"], self.pen.pk)
        data = self.client.get(reverse("pos:api_products"), {"q": "boo"}).json()
        self.assertEqual([p["name"] for p in data["results"]], ["Book"])

    def test_checkout_api_and_receipt(self):
        self.client.force_login(self.cashier)
        response = self.client.post(reverse("pos:api_checkout"), json.dumps({
            "lines": [{"id": self.pen.pk, "qty": 2}], "payments": [{"method": "cash", "amount": "50"}],
            "discount_percent": "0", "customer_name": "Rahim",
        }), content_type="application/json")
        data = response.json()
        self.assertTrue(data["ok"], data)
        self.assertEqual((data["total"], data["change"]), ("20.00", "30.00"))
        receipt = self.client.get(data["receipt_url"])
        self.assertContains(receipt, "Rahim")
        self.assertContains(receipt, "TOTAL")
        bad = self.client.post(reverse("pos:api_checkout"), json.dumps({
            "lines": [{"id": self.pen.pk, "qty": 1}], "payments": [{"method": "cash", "amount": "1"}],
        }), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("short", bad.json()["error"])

    def test_checkout_api_discount_amount_and_adjustment(self):
        self.client.force_login(self.cashier)
        data = self.client.post(reverse("pos:api_checkout"), json.dumps({
            "lines": [{"id": self.book.pk, "qty": 2}], "payments": [{"method": "cash", "amount": "88"}],
            "discount_amount": "10", "adjustment": "2",
        }), content_type="application/json").json()
        self.assertTrue(data["ok"], data)
        self.assertEqual((data["total"], data["change"]), ("88.00", "0.00"))
        sale = Sale.objects.get()
        self.assertEqual((sale.discount_percent, sale.adjustment), (D("10.00"), D("2.00")))
        receipt = self.client.get(data["receipt_url"])
        self.assertContains(receipt, "Discount (10.0%)")
        self.assertContains(receipt, "Adjustment")

    def test_pages_render(self):
        self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        self.client.force_login(self.cashier)
        for name in ("pos:terminal", "pos:sales", "pos:cash", "pos:close"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("pos:session", args=[self.session.pk])).status_code, 200)
        self.client.post(reverse("pos:close"), {"counted_cash": "110"})
        self.assertFalse(RegisterSession.objects.get(pk=self.session.pk).is_open)

    def test_void_view_requires_permission(self):
        sale = self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.post(reverse("pos:void", args=[sale.pk]), {"reason": "x"}).status_code, 403)
        self.client.force_login(self.boss)
        self.client.post(reverse("pos:void", args=[sale.pk]), {"reason": "x"})
        self.assertTrue(Sale.objects.get(pk=sale.pk).is_voided)

    def test_menu_link_registered(self):
        self.client.force_login(self.cashier)
        self.assertContains(self.client.get(reverse("core:home")), reverse("pos:terminal"))

    def test_admin_pages(self):
        self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        self.client.force_login(self.boss)
        for name in ("register", "registersession", "sale", "cashmovement", "barcode"):
            self.assertEqual(self.client.get(reverse(f"admin:pos_{name}_changelist")).status_code, 200, name)

    def test_deleting_product_is_not_blocked(self):
        sale = self.sell([(self.pen.pk, 1)], [("cash", D("10"))])
        void_sale(sale, self.boss, "cleanup")
        sale.order.delete()  # order items go; POS lines keep their snapshot
        self.pen.stock_movements.all().delete()
        self.pen.delete()
        self.assertEqual(Sale.objects.get(pk=sale.pk).lines.get().product_name, "Pen")
