from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Product
from apps.catalog.tests import make_product
from apps.orders.models import Order, OrderItem

from .models import Review
from .services import rating_summary


def make_user(username="ada", **kwargs):
    return User.objects.create_user(username, f"{username}@example.com", "pw-123456!", **kwargs)


def make_review(product, user, rating, **kwargs):
    kwargs.setdefault("body", "Perfectly fine product.")
    return Review.objects.create(product=product, user=user, rating=rating, **kwargs)


def make_order(user, product, status=Order.Status.PENDING):
    order = Order.objects.create(
        user=user, full_name="Ada", email="ada@example.com", address_line1="1 St", city="Dhaka",
        postal_code="1200", country="BD", subtotal=product.price, shipping_cost=Decimal("0"),
        total=product.price, status=status,
    )
    OrderItem.objects.create(order=order, product=product, product_name=product.name,
                             unit_price=product.price, quantity=1)
    return order


class ReviewSubmitTests(TestCase):
    def setUp(self):
        self.product = make_product(name="Lamp", slug="lamp")
        self.user = make_user()
        self.url = reverse("reviews:submit", args=[self.product.slug])
        self.data = {"rating": "4", "title": "Nice", "body": "Bright and well made lamp."}

    def test_anonymous_redirected_to_login(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertIn("next=%2Fproducts%2Flamp%2F%23reviews", response["Location"])
        self.assertFalse(Review.objects.exists())

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_submit_creates_then_updates(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, f"{self.product.get_absolute_url()}#reviews", fetch_redirect_response=False)
        review = Review.objects.get()
        self.assertEqual((review.rating, review.title, review.user), (4, "Nice", self.user))
        self.assertFalse(review.verified_purchase)

        self.client.post(self.url, {**self.data, "rating": "2", "body": "Changed my mind about it."})
        review = Review.objects.get()
        self.assertEqual(review.rating, 2)

    def test_verified_purchase(self):
        make_order(self.user, self.product)
        self.client.force_login(self.user)
        self.client.post(self.url, self.data)
        self.assertTrue(Review.objects.get().verified_purchase)

    def test_cancelled_order_not_verified(self):
        make_order(self.user, self.product, status=Order.Status.CANCELLED)
        self.client.force_login(self.user)
        self.client.post(self.url, self.data)
        self.assertFalse(Review.objects.get().verified_purchase)

    def test_invalid_rating_and_body_rejected(self):
        self.client.force_login(self.user)
        for bad in ({"rating": "0"}, {"rating": "6"}, {"rating": ""}, {"body": "short"}):
            response = self.client.post(self.url, {**self.data, **bad})
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Review.objects.exists())
        self.assertContains(response, "Please write at least 10 characters.")

    def test_delete_own_review_only(self):
        other = make_user("bob")
        make_review(self.product, other, 5)
        mine = make_review(self.product, self.user, 1)
        self.client.force_login(self.user)
        self.client.post(reverse("reviews:delete", args=[self.product.slug]))
        self.assertFalse(Review.objects.filter(pk=mine.pk).exists())
        self.assertEqual(Review.objects.count(), 1)

    def test_one_review_per_user_per_product(self):
        make_review(self.product, self.user, 5)
        with self.assertRaises(IntegrityError):
            make_review(self.product, self.user, 3)


class RatingDisplayTests(TestCase):
    def setUp(self):
        self.product = make_product(name="Lamp", slug="lamp")
        make_review(self.product, make_user("a", first_name="Ayesha", last_name="Rahman"), 5, title="Superb")
        make_review(self.product, make_user("b"), 4)
        make_review(self.product, make_user("c"), 3)
        make_review(self.product, make_user("d"), 1, is_approved=False, title="Hidden one")

    def test_summary_excludes_unpublished(self):
        summary = rating_summary(self.product)
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["average"], 4.0)
        self.assertEqual(summary["distribution"][0], (5, 1, 33))
        self.assertEqual(summary["distribution"][4], (1, 0, 0))

    def test_annotations(self):
        product = Product.objects.for_display().get(pk=self.product.pk)
        self.assertEqual(product.rating_count, 3)
        self.assertAlmostEqual(float(product.rating_avg), 4.0)

    def test_detail_page(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, 'id="reviews"')
        self.assertContains(response, "Based on 3 reviews")
        self.assertContains(response, "Superb")
        self.assertContains(response, "Ayesha R.")
        self.assertNotContains(response, "Hidden one")
        self.assertContains(response, "Log in to write a review")

    def test_own_hidden_review_shows_notice(self):
        self.client.force_login(User.objects.get(username="d"))
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "Your review")
        self.assertContains(response, "hidden from other shoppers")

    def test_review_sorting_and_pagination(self):
        for i in range(5):
            make_review(self.product, make_user(f"x{i}"), 2, title=f"Extra {i}")
        url = self.product.get_absolute_url()
        lowest = self.client.get(url, {"reviews_sort": "lowest"}).content.decode()
        self.assertLess(lowest.index("Extra"), lowest.index("Superb") if "Superb" in lowest else len(lowest))
        highest = self.client.get(url, {"reviews_sort": "highest"})
        self.assertContains(highest, "Superb")
        self.assertContains(highest, "Showing 1–5 of 8")
        self.assertContains(self.client.get(url, {"reviews_page": 2}), "Showing 6–8 of 8")

    def test_card_shows_rating_and_top_rated_sort(self):
        low = make_product(name="Dim Lamp", slug="dim-lamp")
        make_review(low, make_user("e"), 2)
        make_product(name="Unrated Lamp", slug="unrated-lamp")
        response = self.client.get(reverse("catalog:product_list"), {"sort": "rating"})
        self.assertContains(response, "(3)")
        html = response.content.decode()
        self.assertLess(html.index(">Lamp<"), html.index("Dim Lamp"))
        self.assertLess(html.index("Dim Lamp"), html.index("Unrated Lamp"))

    def test_admin_changelist(self):
        self.client.force_login(User.objects.create_superuser("root", "r@example.com", "pw"))
        self.assertContains(self.client.get(reverse("admin:reviews_review_changelist")), "Superb")


class SeedReviewsTests(TestCase):
    def test_seed_creates_reviews_once(self):
        call_command("seed_demo", no_images=True, stdout=StringIO())
        count = Review.objects.count()
        self.assertGreaterEqual(count, Product.objects.count() * 2)
        self.assertFalse(User.objects.get(username="demo_ayesha").has_usable_password())
        call_command("seed_demo", no_images=True, stdout=StringIO())
        self.assertEqual(Review.objects.count(), count)
