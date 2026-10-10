from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AccountTests(TestCase):
    def test_signup_logs_in(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {"username": "grace", "email": "grace@example.com",
             "password1": "s3cure-Passw0rd", "password2": "s3cure-Passw0rd"},
        )
        self.assertRedirects(response, reverse("core:home"))
        self.assertTrue(User.objects.filter(username="grace").exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get().pk)

    def test_login_and_logout(self):
        User.objects.create_user("grace", "g@example.com", "s3cure-Passw0rd")
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)
        response = self.client.post(reverse("accounts:login"), {"username": "grace", "password": "s3cure-Passw0rd"})
        self.assertRedirects(response, reverse("core:home"))
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_orders_requires_login(self):
        response = self.client.get(reverse("accounts:orders"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('accounts:orders')}")

    def test_404_page(self):
        response = self.client.get("/does-not-exist/")
        self.assertEqual(response.status_code, 404)
