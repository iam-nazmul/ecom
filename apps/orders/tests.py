from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.tests import make_product

from .models import Order
from .services import shipping_for

ADDRESS = {
    "full_name": "Ada Lovelace",
    "email": "ada@example.com",
    "phone": "",
    "address_line1": "1 Analytical St",
    "address_line2": "",
    "city": "London",
    "postal_code": "N1",
    "country": "UK",
    "notes": "",
}


@override_settings(SHIPPING_FLAT_RATE=Decimal("5.00"), FREE_SHIPPING_THRESHOLD=Decimal("50.00"))
class ShippingTests(TestCase):
    def test_rules(self):
        self.assertEqual(shipping_for(Decimal("0")), Decimal("0.00"))
        self.assertEqual(shipping_for(Decimal("49.99")), Decimal("5.00"))
        self.assertEqual(shipping_for(Decimal("50.00")), Decimal("0.00"))


@override_settings(SHIPPING_FLAT_RATE=Decimal("5.00"), FREE_SHIPPING_THRESHOLD=Decimal("50.00"))
class CheckoutTests(TestCase):
    def setUp(self):
        self.product = make_product(price=Decimal("15.00"), stock=4)

    def add_to_cart(self, qty):
        self.client.post(reverse("cart:add", args=[self.product.pk]), {"quantity": qty})

    def test_empty_cart_redirects(self):
        response = self.client.get(reverse("orders:checkout"))
        self.assertRedirects(response, reverse("cart:detail"))

    def test_guest_checkout_places_order(self):
        self.add_to_cart(2)
        response = self.client.post(reverse("orders:checkout"), ADDRESS)
        order = Order.objects.get()
        self.assertRedirects(response, order.get_absolute_url())
        self.assertEqual(order.subtotal, Decimal("30.00"))
        self.assertEqual(order.shipping_cost, Decimal("5.00"))
        self.assertEqual(order.total, Decimal("35.00"))
        self.assertEqual(order.items.get().quantity, 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)
        self.assertEqual(self.client.session["cart"], {})
        self.assertContains(self.client.get(order.get_absolute_url()), order.reference)

    def test_insufficient_stock_writes_nothing(self):
        self.add_to_cart(3)
        self.product.stock = 1
        self.product.save()
        response = self.client.post(reverse("orders:checkout"), ADDRESS)
        self.assertRedirects(response, reverse("cart:detail"))
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)

    def test_invalid_form_rerenders(self):
        self.add_to_cart(1)
        response = self.client.post(reverse("orders:checkout"), {**ADDRESS, "email": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_order_detail_private(self):
        self.add_to_cart(1)
        self.client.post(reverse("orders:checkout"), ADDRESS)
        order = Order.objects.get()
        self.client.cookies.clear()
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)

    def test_logged_in_order_linked_to_user(self):
        user = User.objects.create_user("ada", "ada@example.com", "pw-123456!")
        self.client.force_login(user)
        self.add_to_cart(1)
        self.client.post(reverse("orders:checkout"), ADDRESS)
        self.assertEqual(Order.objects.get().user, user)
        self.assertContains(self.client.get(reverse("accounts:orders")), Order.objects.get().reference)
