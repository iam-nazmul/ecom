from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.tests import make_product


@override_settings(CURRENCY_SYMBOL="৳")
class CartTests(TestCase):
    def setUp(self):
        self.product = make_product(price=Decimal("12.50"), stock=3)

    def cart_data(self):
        return self.client.session.get("cart", {})

    def test_add_and_view(self):
        self.client.post(reverse("cart:add", args=[self.product.pk]), {"quantity": 2})
        self.assertEqual(self.cart_data(), {str(self.product.pk): 2})
        response = self.client.get(reverse("cart:detail"))
        self.assertContains(response, "Widget")
        self.assertContains(response, "৳25.00")

    def test_quantity_clamped_to_stock(self):
        self.client.post(reverse("cart:add", args=[self.product.pk]), {"quantity": 10})
        self.assertEqual(self.cart_data()[str(self.product.pk)], 3)

    def test_update_to_zero_removes(self):
        self.client.post(reverse("cart:add", args=[self.product.pk]))
        self.client.post(reverse("cart:update", args=[self.product.pk]), {"quantity": 0})
        self.assertEqual(self.cart_data(), {})

    def test_remove(self):
        self.client.post(reverse("cart:add", args=[self.product.pk]))
        self.client.post(reverse("cart:remove", args=[self.product.pk]))
        self.assertEqual(self.cart_data(), {})

    def test_out_of_stock_not_added(self):
        self.product.stock = 0
        self.product.save()
        self.client.post(reverse("cart:add", args=[self.product.pk]))
        self.assertEqual(self.cart_data(), {})

    def test_add_requires_post(self):
        self.assertEqual(self.client.get(reverse("cart:add", args=[self.product.pk])).status_code, 405)

    def test_open_redirect_blocked(self):
        response = self.client.post(
            reverse("cart:add", args=[self.product.pk]), {"next": "https://evil.example.com/"}
        )
        self.assertRedirects(response, reverse("cart:detail"), fetch_redirect_response=False)
